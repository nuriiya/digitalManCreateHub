# 数字人全链路平台 · 系统设计（design.md）

> **文档版本**：v1.4（2026-09-11）· 状态：**§11 / §12 / §13 已实现；§10 协议层 + JSON 解析器收敛已实现（业务链路切换未做）；§15 设计已定、未实现。逐项见 §9.6**
> **配套文档**：需求见 `docs/requirement.md`；测试指标见 `docs/test-metrics.md`（Test Metrics）。
> **维护约定（硬性）**：任何设计变更（架构/流程/实体/指标/入口）都必须**同步更新**本文件与 `docs/requirement.md`、`docs/test-metrics.md` 三份文档的对应条目；代码提交前先改文档，或在同一提交内完成（详见 §14）。

---

## 1. 系统概览与设计哲学

### 1.1 定位

**数字人全链路平台**：以「部门文档 → RAG 知识库 → 本体 → 数字人」为主链，把**非结构化的领域知识**组装成**可审批、可测试、可版本回滚、可编排协作、可调用外部工具（MCP）**的「数字人」。每个数字人是**六元组封装体**，平台同时提供对话、能力测试、自动迭代训练、pipeline 协作编排、四组对照评测与调研/知识入库等外围子系统。

### 1.2 三条铁律（安全底座，所有子系统的共同约束）

| # | 铁律 | 含义 | 落地机制 |
|---|---|---|---|
| L1 | **LLM 无终审权** | 任何 LLM 输出都是「提名」，由确定性代码裁决，用户是唯一终审点 | 全链路 `LLM 提名 → 确定性三关/闭集校验 → 用户审批` |
| L2 | **不知道就说不知道** | 本体未覆盖的内容明确拒答，绝不编造 | 对话 system prompt 铁律；benchmark `refused` 类别 |
| L3 | **绝对准确/证据可引** | 知识必须逐字可回溯到原文，禁止凭空生成 | chunks 证据 span、`mentions` 逐字命中、RAG 回取原文 |

设计推论（贯穿全系统）：

- **代码定路径，LLM 只做节点**：路由、检索打分、闭集校验、拓扑、判定模板全部 0-LLM 确定性代码。
- **三关校验**（结构闭集/语义引用/证据 span）是本体入池的通用闸门。
- **降级链 vs 终审链**：能力/检索的 LLM 失败可降级（不影响主链）；判别/考核的 LLM 失败**不静默兜底**（避免同源自评，异源交叉核验）。

### 1.3 总体架构（分层）

<svg viewBox="0 0 680 470" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="总体分层架构图">
<rect x="0" y="0" width="680" height="470" fill="#FFFFFF"/>
<defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">数字人全链路平台 · 分层架构</text>
<rect x="40" y="42" width="600" height="66" rx="10" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/>
<text x="56" y="60" font-size="12" fill="#0C447C">前端 React · Vite（深色主题，8 页 + 对话/编排/MCP/图谱）</text>
<text x="56" y="78" font-size="12" fill="#185FA5">会话流式 SSE · WebSocket 事件 · REST /api/*</text>
<text x="56" y="96" font-size="12" fill="#185FA5">气泡对话 / 本体图谱 ReactFlow / pipeline SVG 编排 / MCP 面板</text>
<path d="M340 108 L340 126" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arr)"/>
<rect x="40" y="128" width="600" height="170" rx="10" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/>
<text x="56" y="146" font-size="12" fill="#3C3489">后端 Python · FastAPI（单一进程；PG 全量存储；jobs = 一切长任务）</text>
<g font-size="11">
<rect x="54" y="156" width="138" height="52" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="123" y="175" text-anchor="middle" font-weight="600" fill="#3C3489">内容工程</text><text x="123" y="192" text-anchor="middle" fill="#534AB7">ingest/chunking/embedding</text><text x="123" y="205" text-anchor="middle" fill="#534AB7">ontology/assembly</text>
<rect x="198" y="156" width="138" height="52" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="267" y="175" text-anchor="middle" font-weight="600" fill="#3C3489">数字人对话</text><text x="267" y="192" text-anchor="middle" fill="#534AB7">chat（动态检索窗口）</text><text x="267" y="205" text-anchor="middle" fill="#534AB7">actions 动作循环</text>
<rect x="342" y="156" width="138" height="52" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="411" y="175" text-anchor="middle" font-weight="600" fill="#3C3489">测试·训练</text><text x="411" y="192" text-anchor="middle" fill="#534AB7">benchmark/capability</text><text x="411" y="205" text-anchor="middle" fill="#534AB7">trainer/exam</text>
<rect x="486" y="156" width="138" height="52" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="555" y="175" text-anchor="middle" font-weight="600" fill="#3C3489">编排·工具</text><text x="555" y="192" text-anchor="middle" fill="#534AB7">pipeline 编排</text><text x="555" y="205" text-anchor="middle" fill="#534AB7">MCP 沙盒/mcp_repo</text>
<rect x="54" y="218" width="138" height="68" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="123" y="236" text-anchor="middle" font-weight="600" fill="#3C3489">调研入库</text><text x="123" y="253" text-anchor="middle" fill="#534AB7">research 多源搜索</text><text x="123" y="267" text-anchor="middle" fill="#534AB7">置信度分级聚合</text>
<rect x="198" y="218" width="138" height="68" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="267" y="236" text-anchor="middle" font-weight="600" fill="#3C3489">本体治理</text><text x="267" y="253" text-anchor="middle" fill="#534AB7">身份预筛/锚点</text><text x="267" y="267" text-anchor="middle" fill="#534AB7">二次编排/版本快照</text>
<rect x="342" y="218" width="138" height="68" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="411" y="236" text-anchor="middle" font-weight="600" fill="#3C3489">任务基建</text><text x="411" y="253" text-anchor="middle" fill="#534AB7">jobs/events 事件流</text><text x="411" y="267" text-anchor="middle" fill="#534AB7">checkpoint 续跑</text>
<rect x="486" y="218" width="138" height="68" rx="7" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="555" y="236" text-anchor="middle" font-weight="600" fill="#3C3489">平台服务</text><text x="555" y="253" text-anchor="middle" fill="#534AB7">auth/settings</text><text x="555" y="267" text-anchor="middle" fill="#534AB7">backup 导出导入</text>
</g>
<path d="M340 298 L340 316" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arr)"/>
<rect x="40" y="318" width="292" height="62" rx="10" fill="#FAEEDA" stroke="#854F0B" stroke-width="1"/>
<text x="186" y="338" text-anchor="middle" font-size="13" font-weight="600" fill="#633806">LLM 通道（双通道）</text>
<text x="186" y="356" text-anchor="middle" font-size="11" fill="#854F0B">V4-Flash = 提名/高频（llm）</text>
<text x="186" y="370" text-anchor="middle" font-size="11" fill="#854F0B">GLM 5.2 = 判别/对话（llm2）</text>
<rect x="348" y="318" width="292" height="62" rx="10" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/>
<text x="494" y="338" text-anchor="middle" font-size="13" font-weight="600" fill="#085041">本地/嵌入（Ollama）</text>
<text x="494" y="356" text-anchor="middle" font-size="11" fill="#0F6E56">bge-m3 embedding</text>
<text x="494" y="370" text-anchor="middle" font-size="11" fill="#0F6E56">qwen2.5:7b（幻觉 A/B 基线）</text>
<path d="M340 380 L340 398" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arr)"/>
<rect x="40" y="400" width="600" height="56" rx="10" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="1"/>
<text x="340" y="420" text-anchor="middle" font-size="13" font-weight="600" fill="#444441">PostgreSQL + pgvector（单一事实源：数据态 + 任务态 + 配置全在 PG）</text>
<text x="340" y="442" text-anchor="middle" font-size="11" fill="#5F5E5A">全 docker：rag_backend / rag_pg / rag_ollama（dev 复用宿主 Ollama）</text>
</svg>

架构要点：

1. **任务态与数据态合并在 PG**（由旧 SQLite 全量迁移而来）：documents/chunks、candidates、identities、persona_ontology、chat、pipeline、capability、jobs/events 全部在 PostgreSQL；embedding 向量用 pgvector。
2. **一切长操作皆 job**：摄取、本体提取、考核、评测、pipeline 运行、MCP 镜像构建等统一走 `jobs` + 事件流 + checkpoint（断点续跑）。
3. **LLM 只出现在节点位置**：确定性代码（检索、路由、校验、判分、拓扑）永不调用 LLM；LLM 永不直接落库。
4. **对话默认流式（SSE）**：`/api/chat/stream` 逐 token 推送，路由与数字人身份过程可见（见 §3.4）。

---

## 2. 核心领域模型：数字人 = 六元组封装体

### 2.1 六元组定义

| 元 | 内容 | 写死/填位 | 落库 |
|---|---|---|---|
| **identity** | 名字 / 使命 / 定位 / prompt 附加指令 | 平台模板写死（基础）或用户定义 | `identities` |
| **ontology** | 该数字人只掌握、只能依据的知识集（实体 + 定义 + 关系） | 部门知识包/装配产出 | `persona_ontology` + `relations` |
| **actions** | 可用动作（tool schema：name/description/input_schema），含内置检索动作 + MCP 工具绑定 | 模板 + 审批 | `persona_actions` |
| **guardians** | 确定性护栏（三关校验、参数 schema 校验、沙箱边界），**无 LLM 终审权** | 模板写死 | 代码 + 规则 |
| **interface** | 对外调用面（对话、能力题、pipeline 节点接缝） | 模板 + 填位 | 代码约定 |
| **reflection** | 自我复盘：AI 独白 → 洞察/改进提名 → 回流 Spec 重编译（禁热更） | 模板写死 | 见 §9（未全落地） |

### 2.2 封装示意

<svg viewBox="0 0 680 320" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="六元组封装体">
<rect x="0" y="0" width="680" height="320" fill="#FFFFFF"/>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">数字人 = 六元组封装体（Palantir 五原语 + Reflection）</text>
<rect x="30" y="44" width="300" height="256" rx="12" fill="#F4F1EA" stroke="#B4B2A9" stroke-width="1"/>
<text x="180" y="66" text-anchor="middle" font-size="13" font-weight="600" fill="#444441">平台写死（模板）</text>
<rect x="48" y="80" width="264" height="44" rx="8" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="180" y="100" text-anchor="middle" font-size="13" fill="#633806">identity（名字/使命/定位）</text><text x="180" y="116" text-anchor="middle" font-size="11" fill="#854F0B">模板写死；用户可加 prompt 尾</text>
<rect x="48" y="132" width="264" height="44" rx="8" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="180" y="152" text-anchor="middle" font-size="13" fill="#633806">guardians（三关/闭集/沙箱）</text><text x="180" y="168" text-anchor="middle" font-size="11" fill="#854F0B">确定性代码，LLM 无终审权</text>
<rect x="48" y="184" width="264" height="44" rx="8" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="180" y="204" text-anchor="middle" font-size="13" fill="#633806">reflection（自我复盘/回流）</text><text x="180" y="220" text-anchor="middle" font-size="11" fill="#854F0B">每数字人独立，不共享</text>
<rect x="48" y="236" width="264" height="44" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/><text x="180" y="256" text-anchor="middle" font-size="13" fill="#444441">interface（调用面：对话/能力题/pipeline 接缝）</text><text x="180" y="272" text-anchor="middle" font-size="11" fill="#5F5E5A">模板 + 填位</text>
<rect x="350" y="44" width="300" height="256" rx="12" fill="#EEFBF5" stroke="#0F6E56" stroke-width="1"/>
<text x="500" y="66" text-anchor="middle" font-size="13" font-weight="600" fill="#085041">部门知识包 / 装配（数字人专属）</text>
<rect x="368" y="80" width="264" height="44" rx="8" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="500" y="100" text-anchor="middle" font-size="13" fill="#085041">ontology（只掌握的知识集）</text><text x="500" y="116" text-anchor="middle" font-size="11" fill="#0F6E56">装配三段筛 → persona_ontology</text>
<rect x="368" y="132" width="264" height="44" rx="8" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="500" y="152" text-anchor="middle" font-size="13" fill="#085041">actions（工具：检索/RAG/执行）</text><text x="500" y="168" text-anchor="middle" font-size="11" fill="#0F6E56">builtin + MCP 绑定 + run_code 等</text>
<rect x="368" y="184" width="264" height="44" rx="8" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="500" y="204" text-anchor="middle" font-size="13" fill="#085041">anchors（锚点：核心关注）</text><text x="500" y="220" text-anchor="middle" font-size="11" fill="#0F6E56">身份预筛产出，固定注入</text>
<rect x="368" y="236" width="264" height="60" rx="8" fill="#E6F1FB" stroke="#85B7EB" stroke-width="1"/><text x="500" y="256" text-anchor="middle" font-size="13" fill="#0C447C">relations（概念间关联）</text><text x="500" y="274" text-anchor="middle" font-size="11" fill="#185FA5">本体↔本体 / 本体→文本</text>
</svg>

### 2.3 数字人分类：本体从哪来

| 维度 | 基础数字人（平台级） | 专业数字人（部门级） |
|---|---|---|
| ontology 段来源 | 平台通用本体库（**当前缺，见 §9 N1**） | 部门文档 → RAG + 本体分解器归纳（**已实现**） |
| 创建路径 | Spec 编译五步（**设计已定、代码未实现**） | 模板 + 部门知识包合成（已实现） |
| 六元组 | 平台全写死 | identity/guardians/reflection 写死，ontology/interface/actions 部门填 |
| 数量级 | 少而稳定 | 每部门一个，持续增长 |
| 代码状态 | **未实现（无入口）** | 全链路落地（本仓库主体） |

> **唯一接口**：专业数字人可「引用」平台通用本体但禁止「改写」（引用悬空即拒绝创建）——当前代码中该约束尚未实现（§9 N5）。

---

## 3. 内容工程子系统（主链）

### 3.1 文档加载与 RAG 摄取管线

入口：RagPage 拖拽/目录上传 → `POST /api/rag/upload-files`（**两步**：dry-run 看 conflicts → 带 overwrite_names 确认）；支持 md / pdf / text / xlsx / csv / docx。

摄取链路（`ingest.py`，原子单位 = 一个 chunk）：

<svg viewBox="0 0 680 210" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="RAG 摄取链路">
<rect x="0" y="0" width="680" height="210" fill="#FFFFFF"/>
<defs><marker id="a2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">RAG 摄取：分段 → 摘要+标签 → 聚合 → 入库 → 检索回取</text>
<g font-size="11">
<rect x="24" y="52" width="120" height="52" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/><text x="84" y="70" text-anchor="middle" font-weight="600" fill="#0C447C">① 加载+分段</text><text x="84" y="86" text-anchor="middle" fill="#185FA5">sentence 边界+重叠</text><text x="84" y="98" text-anchor="middle" fill="#185FA5">长句硬切</text>
<path d="M144 78 H160" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a2)"/>
<rect x="162" y="52" width="120" height="52" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/><text x="222" y="70" text-anchor="middle" font-weight="600" fill="#3C3489">② summary+标签</text><text x="222" y="86" text-anchor="middle" fill="#534AB7">LLM 逐段生成</text><text x="222" y="98" text-anchor="middle" fill="#534AB7">标签=领域 6 类闭集</text>
<path d="M282 78 H298" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a2)"/>
<rect x="300" y="52" width="120" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/><text x="360" y="70" text-anchor="middle" font-weight="600" fill="#085041">③ embedding</text><text x="360" y="86" text-anchor="middle" fill="#0F6E56">summary 向量化</text><text x="360" y="98" text-anchor="middle" fill="#0F6E56">bge-m3（锁定唯一模型）</text>
<path d="M420 78 H436" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a2)"/>
<rect x="438" y="52" width="120" height="52" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="1"/><text x="498" y="70" text-anchor="middle" font-weight="600" fill="#444441">④ 入库 PG</text><text x="498" y="86" text-anchor="middle" fill="#5F5E5A">三层：整篇/段/原文</text><text x="498" y="98" text-anchor="middle" fill="#5F5E5A">chunks + source_meta</text>
<path d="M540 104 V126 H360" fill="none" stroke="#5F5E5A" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#a2)"/>
<rect x="24" y="128" width="600" height="56" rx="8" fill="#FFF8E6" stroke="#854F0B" stroke-width="1"/>
<text x="324" y="150" text-anchor="middle" font-size="12" fill="#633806">检索（0 LLM）：查询 embedding → 余弦 ≤&gt; 命中 summary → 沿 doc_id/seq 回取原文</text>
<text x="324" y="170" text-anchor="middle" font-size="11" fill="#854F0B">证据可引用（铁律 L3）：命中一定带回 doc 原文，供 LLM 逐字引用</text>
</g>
<text x="340" y="200" text-anchor="middle" font-size="11" fill="#5F5E5A">去重键=文档 name：hash 相同跳过；同名不同 hash → 冲突（dry-run 呈现，二次确认覆盖）</text>
</svg>

关键设计：

- **对 summary 做 embedding，而非原文**：先命中摘要（短、语义集中）再回取原文，检索准、标签可分类。
- **去重与覆盖契约**（扫描入库不可改语义）：去重键 = `name`；覆盖 = `DELETE chunks`（mentions 级联）+ `UPDATE documents` 保 id + `INSERT 新 chunks`；三方互斥（scan/repair/ontology 同时只能一个，`active_job_in_set`）。
- **embedding 锁定**：bge-m3，从首次 ingest 写入 kv 锁定（铁则 1），保证向量可比。
- **embedding 失败降级**：hash embedding 兜底（不中断主链）。
- **chunk 原子提交**：每 chunk = 一个 PG 事务（行 + 进度 + checkpoint 同事务）。
- **chunk 内容类型（type，见 §11）**：每个 chunk 除 `summary`/`tags` 外，还落 `type`（内容性质，词表项）与其派生的 `type_confidence`（置信度）/ `type_mandatory`（强制等级）；`mandatory=2` 的知识在对话注入中**必选**，不受 top-k 截断。
- **摄取契约人格化（见 §12）**：上述分块/去重/类型/证据规则统一封装为「知识摄取官」数字人的本体，**规则可审批可复盘，但执行仍由本链路确定性完成**。

### 3.2 本体分解器（EDC-lite）与治理

`ontology.py`：LLM 候选提名 → 确定性三关 → 分级审批 → 状态机。

<svg viewBox="0 0 680 300" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="本体提取与治理管线">
<rect x="0" y="0" width="680" height="300" fill="#FFFFFF"/>
<defs><marker id="a3" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">本体治理链：抽取 → 三关 → 二次编排 → 装配 → 审批生效</text>
<g font-size="11">
<rect x="24" y="48" width="120" height="52" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/><text x="84" y="66" text-anchor="middle" font-weight="600" fill="#3C3489">抽取候选</text><text x="84" y="82" text-anchor="middle" fill="#534AB7">V4-Flash open 抽取</text><text x="84" y="94" text-anchor="middle" fill="#534AB7">带 mentions 证据</text>
<path d="M144 74 H160" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a3)"/>
<rect x="162" y="48" width="120" height="52" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="1"/><text x="222" y="66" text-anchor="middle" font-weight="600" fill="#633806">三关校验</text><text x="222" y="82" text-anchor="middle" fill="#854F0B">结构(闭集/类型/长度)</text><text x="222" y="94" text-anchor="middle" fill="#854F0B">语义(引用/基数) 证据(span)</text>
<path d="M282 74 H298" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a3)"/>
<rect x="300" y="48" width="120" height="52" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/><text x="360" y="66" text-anchor="middle" font-weight="600" fill="#0C447C">二次编排</text><text x="360" y="82" text-anchor="middle" fill="#185FA5">规则+GLM 分诊</text><text x="360" y="94" text-anchor="middle" fill="#185FA5">删/并/留 提名</text>
<path d="M420 74 H436" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a3)"/>
<rect x="438" y="48" width="120" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/><text x="498" y="66" text-anchor="middle" font-weight="600" fill="#085041">本体装配</text><text x="498" y="82" text-anchor="middle" fill="#0F6E56">三段筛：锚点→排除→分诊</text><text x="498" y="94" text-anchor="middle" fill="#0F6E56">persona_ontology</text>
<path d="M558 74 H574" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a3)"/>
<rect x="576" y="48" width="80" height="52" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="1"/><text x="616" y="70" text-anchor="middle" font-weight="600" fill="#444441">审批</text><text x="616" y="84" text-anchor="middle" fill="#5F5E5A">用户终审</text>
</g>
<rect x="24" y="112" width="632" height="70" rx="8" fill="#F6F6F4" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="134" text-anchor="middle" font-size="12" fill="#444441">状态机：proposed → approved / rejected / merged（合并）/ suspended（暂缓）</text>
<text x="340" y="152" text-anchor="middle" font-size="12" fill="#444441">多次候选 → 同一实体（name_norm 去重），同名不同义 → 冲突弹窗让用户决</text>
<text x="340" y="170" text-anchor="middle" font-size="11" fill="#5F5E5A">数字化人实体可指定 role 归属（组织架构 type + 优先级），图谱按角色子树聚焦</text>
<rect x="24" y="192" width="632" height="92" rx="8" fill="#FFF4E8" stroke="#FAC775" stroke-width="1"/>
<text x="340" y="212" text-anchor="middle" font-size="12" font-weight="600" fill="#633806">治理闭环（benchmark 回流）</text>
<text x="340" y="232" text-anchor="middle" font-size="11" fill="#854F0B">对话/考核中答错(wrong/partial) → GLM 归因提名问题本体 → 用户逐条审</text>
<text x="340" y="250" text-anchor="middle" font-size="11" fill="#854F0B">→ 一键 merge = 快照当前 → 标注/改定义/软删 → 版本 v+1 → 可回滚（详见 §4.1）</text>
<text x="340" y="272" text-anchor="middle" font-size="11" fill="#854F0B">训练师迭代（capability 跑分不达标）→ 失败归因 → 提名补本体 → 三关 → 装配 → 复测</text>
</svg>

### 3.3 身份预筛与锚点（identity.py）

- 输入：部门语料高频词 → LLM **提名**可能的数字人身份 + 锚点本体（非白名单引导）→ 用户审批。
- 锚点 = 数字人的「核心关注」，对话时**固定全量注入**，路由打分权重 2（本体命中 3）。
- 考核：身份审批前可自动出题考核（quiz + 确定性判分，仅建议，用户终审）。

### 3.4 数字人对话（chat.py）与流式体验

约束装配 + 动态检索窗口 + 路由 + 动作循环：

<svg viewBox="0 0 680 252" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="对话流式与路由">
<rect x="0" y="0" width="680" height="252" fill="#FFFFFF"/>
<defs><marker id="a4" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">对话：路由（0 LLM）→ 动态检索窗口 → SSE 流式生成</text>
<g font-size="11">
<rect x="24" y="44" width="150" height="60" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/><text x="99" y="62" text-anchor="middle" font-weight="600" fill="#0C447C">① 确定性路由</text><text x="99" y="78" text-anchor="middle" fill="#185FA5">消息 ⋈ 各数字人本体/锚点</text><text x="99" y="90" text-anchor="middle" fill="#185FA5">打分最高者胜</text>
<path d="M174 74 H196" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a4)"/>
<rect x="198" y="44" width="226" height="60" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/><text x="311" y="62" text-anchor="middle" font-weight="600" fill="#3C3489">② 动态检索窗口（0 LLM 主干）</text><text x="311" y="78" text-anchor="middle" fill="#534AB7">锚点全放 → L1 命中本体 → L2 命中关系</text><text x="311" y="90" text-anchor="middle" fill="#534AB7">L3/L4 预算驱动扩展（上限 400/深度 2）</text>
<path d="M424 74 H446" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a4)"/>
<rect x="448" y="44" width="208" height="60" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="1"/><text x="552" y="62" text-anchor="middle" font-weight="600" fill="#633806">③ SSE 流式生成</text><text x="552" y="78" text-anchor="middle" fill="#854F0B">/api/chat/stream 逐 token</text><text x="552" y="90" text-anchor="middle" fill="#854F0B">session→token→done/error</text>
</g>
<rect x="24" y="116" width="632" height="54" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="136" text-anchor="middle" font-size="12" fill="#444441">上下文注入顺序：identity → anchors（固定）→ 本体段（检索命中+扩展）→ 关系 → 参考资料(RAG 可选)</text>
<text x="340" y="156" text-anchor="middle" font-size="11" fill="#5F5E5A">RAG 注入独立开关（use_rag），与本体可叠加；检索失败优雅降级</text>
<rect x="24" y="180" width="632" height="58" rx="8" fill="#FFF8E6" stroke="#FAC775" stroke-width="1"/>
<text x="340" y="200" text-anchor="middle" font-size="12" fill="#633806">动作循环（可选，≤3 轮）：GLM 提名 &lt;tool_call&gt; → guard 确定性裁决 → 执行 → 结果回流再答</text>
<text x="340" y="220" text-anchor="middle" font-size="11" fill="#854F0B">actions：ontology_retrieve / rag_retrieve / run_code / run_test / read_file / write_file / MCP 工具</text>
<text x="340" y="234" text-anchor="middle" font-size="11" fill="#854F0B">流式路径不做 tool 循环（tool 型走一次性 /api/chat）——见 requirement R-F-13 边界</text>
</svg>

关键设计：

- **对话历史多会话**：`chat_sessions`/`chat_messages` 按 session 分组，session 可跨数字人（气泡上方标来源 identity_name）。
- **历史不参与本体检索匹配源**（只用当前消息），避免历史长回复引入无关命中（实测命中 4→391 的回归）。
- **概念抽取兜底（#106）**：0 字面命中 → V4-Flash 只抽概念词（3-8 个，不碰实体选择）→ 代码双向包含匹配挑种子。
- **路由失败**：无匹配 → 提示换说法（不静默指派）。
- **provider**：llm2=GLM 默认 / llm=V4-Flash / ollama=本地 7B；`llm_mode` 可切 cloud/local；`LLM_PROXY` 环境变量可选代理（当前容器直连无需代理）。

---

## 4. 测试与训练子系统

### 4.1 四组对照 Benchmark + 归因 + 版本管理（benchmark.py）

**指标定义详见 `docs/test-metrics.md`**。流程五段：选题（0 LLM）→ 四组对照答题（G0 裸 / G1 本体 / G2 RAG / G3 叠加）→ GLM 判卷（闭集提名）→ 确定性终审（refusal 标记覆盖）→ 统计模板 → 归因提名 → 版本管理（快照/merge/回滚，用户终审）。

### 4.2 能力题与可执行验证（capability.py）

- 能力题 = 任务 + 隐藏测试（HumanEval 164 + 项目相关题）。
- **可执行验证铁律**：代码对不对，跑容器 assert 说了算（不是 LLM 猜）——丢一次性 python 容器执行，`verdict ∈ {pass, fail}`。
- **反应式循环**（`reactive=True` 数字人开关）：写 → 跑测试 → 看失败 → 改，最多 3 轮，绿即通过。
- **本体注入解题**：把该数字人 active 本体全量注入 system prompt（不是字面检索），供其写代码依据。
- **沙箱安全边界**：network_disabled / read_only / cap_drop ALL / no-new-privileges / mem 256m / 1 cpu / pids 64。

### 4.3 训练师（trainer.py，Pipeline 训练闭环）

<svg viewBox="0 0 680 232" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="训练师闭环">
<rect x="0" y="0" width="680" height="232" fill="#FFFFFF"/>
<defs><marker id="a5" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">训练师闭环：题集 → 基线 → 迭代（失败归因→补本体→复测）</text>
<g font-size="11">
<rect x="24" y="48" width="130" height="52" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/><text x="89" y="66" text-anchor="middle" font-weight="600" fill="#0C447C">① 选训练集</text><text x="89" y="82" text-anchor="middle" fill="#185FA5">capability_tasks</text><text x="89" y="94" text-anchor="middle" fill="#185FA5">按 persona_role</text>
<path d="M154 74 H172" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a5)"/>
<rect x="174" y="48" width="130" height="52" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/><text x="239" y="66" text-anchor="middle" font-weight="600" fill="#3C3489">② 建基线</text><text x="239" y="82" text-anchor="middle" fill="#534AB7">跑分 samples 多数票</text><text x="239" y="94" text-anchor="middle" fill="#534AB7">pass_rate</text>
<path d="M304 74 H322" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a5)"/>
<rect x="324" y="48" width="160" height="52" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="1"/><text x="404" y="66" text-anchor="middle" font-weight="600" fill="#633806">③ 失败归因</text><text x="404" y="82" text-anchor="middle" fill="#854F0B">LLM 分析失败 → 提名补本体</text><text x="404" y="94" text-anchor="middle" fill="#854F0B">(三关校验后入池)</text>
<path d="M484 74 H502" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#a5)"/>
<rect x="504" y="48" width="152" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/><text x="580" y="66" text-anchor="middle" font-weight="600" fill="#085041">④ 装配+复测</text><text x="580" y="82" text-anchor="middle" fill="#0F6E56">本体入 persona_ontology</text><text x="580" y="94" text-anchor="middle" fill="#0F6E56">重跑看 improvement</text>
</g>
<path d="M580 100 V150 H504" fill="none" stroke="#0F6E56" stroke-width="1.5" marker-end="url(#a5)"/>
<path d="M404 100 V150 H162" fill="none" stroke="#888780" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#a5)"/>
<rect x="24" y="152" width="632" height="60" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="172" text-anchor="middle" font-size="12" fill="#444441">迭代目标：improvement = 复测 pass_rate − 基线 pass_rate（正 = 本体补对了）</text>
<text x="340" y="190" text-anchor="middle" font-size="11" fill="#5F5E5A">多轮迭代直到达标或达上限；每轮只经前端触发（训练师不可绕过审批直接改本体）</text>
<text x="340" y="204" text-anchor="middle" font-size="11" fill="#5F5E5A">能力题通过 → 可选「沉淀为工具」：git commit 进 mcp_repo 工具库（版本可回滚）</text>
</svg>

### 4.4 考核（exam，双 LLM 交叉核验）

- LLM-1 闭卷作答（A/B 消融，有/无本体）→ LLM-2 开卷判别（GLM 异源，禁用 thinking 降时延）→ 确定性三关终审 → verdict ∈ {pass/fail/missing} 建议，用户终审。
- **判别器未配置直接失败**（不同源自评铁律），不静默。

---

## 5. 工具与集成子系统

### 5.1 MCP 沙盒（mcp.py）

- 每个 MCP = 一个 Docker 容器（镜像/命令隔离），注册在 `mcp_servers` 表，审批后才可启动。
- **自动导入**：`mcp_imports/*.json`（模型输出）启动时/手动扫描 → 幂等 upsert（pending 待审批）。
- **_ensure_image**：国内镜像前缀预拉基础镜像 + build，避免 docker-py 隐式 auto-pull 打到失效 daocloud。
- **execute_mcp**：stdio JSON-RPC（写 stdin raw / 读 stdout 8 字节 multiplexed header / SocketIO 解包）；local-only 探测（`host.docker.internal`）走直连，远程域名须代理环境变量。

### 5.2 生成 MCP / 生成 pipeline（LLM 命名 → 确定性落库）

- **/api/mcp/generate**（`mcp.generate_from_request`）：对话页「⚙ 生成 MCP」气泡 → 六阶段（需求→设计→写码→审查→测试→调试）pipeline 协作设计输出包（定义 JSON + server.py + Dockerfile）→ 写 `mcp_imports/{name}.json` + `sandbox/{name}/` → 导入 pending。
- **/api/pipeline/generate**（`pipeline.generate_from_request`）：对话页「🔗 创建 pipeline」气泡 → LLM 设计节点+关系 → create_pipeline + add_node + add_relation → draft 待审批。

### 5.3 pipeline 编排（一等公民实体，pipeline.py）

- 数据模型对称于数字人本体库：`pipelines`/`pipeline_nodes`/`pipeline_relations`/`pipeline_changes`/`pipeline_runs`/`pipeline_run_handoffs`。
- 节点 = persona 或能力（kind）；关系类型：`handoff`（流转）/ `review`（**审核门**：红粗线 + 菱形门）/ `ask`（**询问回退**：蓝虚线，反向边）。
- **ask 是反向边**：必须从环检测 / topo / 布局排除（否则破坏 DAG）。review 参与校验为「门」。
- 运行 = job；跨节点 handoff 记录 schema；对话模式可改流程（LLM 提名 change → 批准/拒绝）。
- 前端 PipelinePage：深色 SVG（节点深底亮字 + kind 配色 + 审核门 + 询问回退 + 自适应布局）。

#### 5.3.1 上下文管理器（context_mgr.py · 平台通用件）

纯代码层默认契约，不建 DB 实体；管住「谁给我什么 / 我给谁什么 / 太长先压缩」，供
pipeline 引擎 / capability reviewer / chat 共用：

- **交接物带 kind**：`pack_handoff` 把节点产出打包为 JSON（kind + content + chars +
  refined + raw_chars），`unpack_handoff` 解包并兼容历史纯文本。kind 闭集：需求规格 /
  技术方案 / 代码实现 / 审查意见 / 失败测试报告 / 任务要求 / 隐藏测试断言 / 调研报告 /
  交接物（generic）。自由文本 handoff_type 经 `normalize_kind` / `KIND_ALIASES` 归一化。
- **预算表**：`KIND_BUDGET`（出站）与 `KIND_INPUT_BUDGET`（入站注入）每 kind 字符上限；
  `budget_for` 未收录回退 DEFAULT_BUDGET。
- **精炼器 refine_handoff**：节点产出超预算 → 由该数字人同模型二次生成「≤预算字交接
  摘要」；LLM 失败/超预算仍超 → `truncate_head_tail` 确定性兜底（保头尾去中段）。
  精炼发生在**产出侧**（上游数字人自缩减后再交下游），非下游硬截断。
- **角色入站白名单** `ROLE_INPUT_KINDS`：按 identity.name 子串匹配（调试 / 测试 / 审查 /
  代码 / 设计 / 需求），命中 kind 才注入；未知角色全收。纯代码默认，后续可落
  persona_interfaces 表个性化。
- **shape_inputs / render_inputs**：下游入站按 kind 分组合并 + 预算裁剪 → 渲染为
  `【kind】…` 分组注入文本。

#### 5.3.2 pipeline 引擎接入（数据上下文传递）

- 节点出站 kind：`_node_out_kind` 优先取非 ask 出边 handoff_type（经 normalize），
  其次按 step_name 推断（NODE_OUT_KIND_BY_STEP）。
- 产出治理 `refine_node_output`：每次节点执行后标 kind + 超预算自缩减 → `store_handoff`
  存打包交接物（含 refined / raw_chars 元数据）。
- **全祖先收集** `_ancestor_node_ids`：collect_inputs 不再只看直接上游，而是沿正向边
  闭包收集全部祖先产物（reviewer 能看到需求方文本，即使隔着设计/编码）。ask 反向边
  不参与数据流。
- 下游注入 `_run_nominate`：shape_inputs（按 kind 分组 + 预算）→ 角色白名单过滤 →
  render_inputs（【kind】标签）→ 拼入 chat.answer，替换旧版「一律 500 字符截断」。
- **4 元消融第 5 组实证**（results_pipe1_fed，2026-09-09）：reviewer 喂饱（任务
  docstring + 隐藏测试断言 + 全量失败输出）后 pass 7/12 与未喂饱持平——R1 诊断质量
  显著提升（精确引用断言 vs 泛泛而谈），但 writer 二次修正成新瓶颈（修正代码崩成
  61-73 字空壳）。结论：单靠喂饱 reviewer 不够，需 writer 修正环节也接入上下文
  （或 reviewer 直接产出补丁而非建议）。

### 5.4 调研链路（research.py，持续调研）

多源搜索 → 置信度分级 → 跨源聚合排序 → 入库 RAG：

- 源：OpenAlex / DBLP / Semantic Scholar / Google Scholar（mcp-paper 服务，OpenAlex + DBLP 无需代理国内直连）。
- **置信度 = 确定性代码裁决**（非 LLM 排序）：期刊 100 > 会议 80 > 预印本 60 > 学位 40。
- 聚合：同题多源按置信度递减合并 → top 结果入库 documents/chunks → 可再喂数字人本体。

### 5.5 能力沉淀（mcp_repo.py）

能力题测试通过 → 提取 description → 写工具代码文件 → **git commit（每次能力一个 commit）** → 三关审批 → 登记为可复用工具（`capability_tools`）。

---

## 6. 平台工程

### 6.1 数据模型（PostgreSQL 全表）

| 域 | 表 |
|---|---|
| 任务/事件 | jobs、events |
| RAG | documents、chunks、**chunk_types**（chunk 内容类型词表，§11）、mentions、kv（元信息/embedding 锁定） |
| 本体池 | candidates、relations、anchors |
| 数字人 | identities、persona_ontology、persona_actions |
| 身份与组织 | users（角色）、anchors |
| 考核/评测 | quiz、exam_runs、exam_results |
| 治理 | orchestration_batches/items、assembly_batches/items |
| 对话 | chat_sessions、chat_messages |
| 评测版本 | persona_benchmarks、persona_benchmark_items、persona_ontology_changes、persona_ontology_versions |
| 编排 | pipelines、pipeline_nodes/relations/changes/runs/run_handoffs |
| 能力 | capability_tasks、capability_runs、capability_tools |
| MCP | mcp_servers |
| 认证 | users、auth_secret（data/） |

关键列：chunks 存 source_meta JSONB（Excel/csv 行列）；persona_ontology 带 source_candidate_id 回指针与 status（active/deprecated）；documents 按 name 唯一（覆盖契约见 §3.1）。

### 6.2 任务系统（jobs.py）

- 一切长操作皆 job；jobs + events 事件流（全局自增 seq）；进度/阶段/错误都成事件。
- 线程模型：`db.get_conn()` = threading.local() 线程本地连接（psycopg3 非线程安全）；schema 只初始化一次；execute 失败无条件 rollback、读(SELECT) 自动 commit、OperationalError 自动重连重试；job 线程退出 reset_conn。
- 并发提取：默认 4，失败 -1（下限 1，连续 8 次成功 +1）；429 重排队 ≤3，非 429 LLMError → auto_pause。
- 事件总线 WS：前端断线指数退避重连 + last_seq 增量补齐。

### 6.3 认证与设置

- `auth.py`：PG 用户 + bcrypt + HMAC-signed bearer token（base64url(username.role.exp.sig)），密钥存 data/auth_secret。
- `settings_store.py`：`data/settings.json`（git-ignored），llm / llm2 / embedding / llm_mode / local_llm；容器内可用 EMBEDDING_BASE_URL / RAG_WORK_DIR 环境变量覆盖；LLM 远程端点支持 LLM_PROXY 可选代理。

### 6.4 备份导出导入（backup.py）

- 全量打包核心业务表（数字人 + 本体 + RAG + pipeline + 能力题/工具）为 JSON → 可 git 提交目录 → .gitattributes 声明大文件走 Git LFS（chunks 原文 + embedding 大字段）。

---

## 7. 前端设计（React · 深色主题）

| 页面 | 职责 |
|---|---|
| LoginPage | 登录 |
| ConversationPage | 对话组：多会话历史 + 自动路由 + **SSE 流式** + markdown + 「生成 MCP / 创建 pipeline」模式气泡 |
| ChatPage | 数字人测试（调试）：选 persona + 本体约束/RAG 开关 + 分屏对比 |
| **IdentityWorkbench** | **数字人工作台**（§13，合并原「数字人创建台」tab 与 IdentityPanel）：列表—详情五 Tab（概览/知识与本体/能力与工具/测试与版本/复盘）+ 创建向导 + 全局任务抽屉 |
| RagPage | RAG 预览：文档库/chunk 明细（含 **type 三维**）/近邻检索/单 chunk 详情 + **类型词表管理**（§11.6） |
| OntologyPage | 本体图谱（ReactFlow + dagre）+ 候选管理 + 二次编排终审门 + 角色子树聚焦（**不再内嵌数字人管理面板**） |
| PipelinePage | pipeline 编排（SVG 图 + 审核门/ask + 对话改流程）+ 训练师面板 |
| McpPage | MCP 沙盒：注册/扫描导入/审批/启停 + tool input_schema + called_by |
| SettingsPage | LLM/判别/Embedding/Ollama/工作目录/改密 |

通用：Toast 提示（ok/err 2.6s）；`/ws/events` 自动重连 + seq 补齐；纯深色主题（styles.css `:root`）；聊天气泡 + md-body markdown + chat-bubble 胶囊输入（mcp-on 绿框 / pipe-on 紫框）。

---

## 8. 部署拓扑（全 docker 为主）

<svg viewBox="0 0 680 300" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="部署拓扑">
<rect x="0" y="0" width="680" height="300" fill="#FFFFFF"/>
<text x="340" y="24" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">部署：全 docker（Docker Desktop）</text>
<rect x="40" y="40" width="600" height="110" rx="10" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/>
<text x="56" y="58" font-size="12" fill="#0C447C">宿主 Windows + Docker Desktop（WSL2 后端）</text>
<g font-size="11">
<rect x="60" y="68" width="180" height="66" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="150" y="86" text-anchor="middle" font-weight="600" fill="#0C447C">rag_backend</text><text x="150" y="102" text-anchor="middle" fill="#185FA5">FastAPI + 前端 dist</text><text x="150" y="116" text-anchor="middle" fill="#185FA5">:8000（dev 挂载源码 --reload）</text>
<rect x="250" y="68" width="180" height="66" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="340" y="86" text-anchor="middle" font-weight="600" fill="#0C447C">rag_pg</text><text x="340" y="102" text-anchor="middle" fill="#185FA5">PG + pgvector</text><text x="340" y="116" text-anchor="middle" fill="#185FA5">:5432（数据态+任务态）</text>
<rect x="440" y="68" width="180" height="66" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="530" y="86" text-anchor="middle" font-weight="600" fill="#0C447C">rag_ollama</text><text x="530" y="102" text-anchor="middle" fill="#185FA5">prod-only</text><text x="530" y="116" text-anchor="middle" fill="#185FA5">dev 复用宿主 Ollama :11434</text>
</g>
<path d="M340 150 V172" fill="none" stroke="#5F5E5A" stroke-width="1.5"/>
<rect x="40" y="172" width="600" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/>
<text x="340" y="192" text-anchor="middle" font-size="12" fill="#085041">backend 挂载 /var/run/docker.sock → 能力测试/MCP 沙盒可启停一次性容器</text>
<text x="340" y="206" text-anchor="middle" font-size="11" fill="#0F6E56">外部：宿主机 Ollama(bge-m3) + LLM 云端点（DeepSeek/GLM，容器直连）</text>
<rect x="40" y="224" width="600" height="60" rx="10" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="242" text-anchor="middle" font-size="12" font-weight="600" fill="#444441">启动脚本族（ASCII-only 铁律：PowerShell 5.1 按 GBK 读无 BOM 文件）</text>
<text x="340" y="260" text-anchor="middle" font-size="11" fill="#5F5E5A">start1.ps1 [dev|prod] [-Rebuild] · start.sh [dev|prod] [--rebuild] · start.ps1（旧 venv 架构，已弃）</text>
<text x="340" y="276" text-anchor="middle" font-size="11" fill="#5F5E5A">dev 复用已有镜像（无 --build 秒起）；改代码后显式 -Rebuild；镜像不存在时 compose 自动 build</text>
</svg>

> 镜像构建（Dockerfile 多阶段）：stage1 node:22-slim 内 `npm install + npm run build`（dist 打进镜像）→ stage2 python:3.13-slim 装 backend 依赖 + COPY dist + entrypoint（先等 PG）。

---

## 9. 已设计未实现（Roadmap · 明示缺口）

> 本节是「设计已存在、代码未落地」的完整清单；新设计一律在文档先定、代码后做，避免口头设计失忆。

### 9.1 基础数字人体系（Spec 编译路径）—— 全部未实现

| ID | 事项 | 说明 | 出处 |
|---|---|---|---|
| N1 | 平台通用本体库（表 + 手工定义入口 + 版本快照回滚） | 基础数字人 ontology 给养源；专业数字人只读引用 | doc/顶层架构 §7 |
| N2 | assembly 取源分支（平台通用本体库取源） | 现仅从 candidates 取源 | doc/顶层架构 §7 |
| N3 | Spec 编译器（元本体校验 + 编译五类产物：prompt/tools/SHACL/状态机/数据模型） | 真正的新增工作量 | doc/顶层架构 §2 |
| N4 | 六元语 actions/guardians/interface 表与版本化 | 现仅 persona_actions | doc/顶层架构 §7 |
| N5 | 本体引用 + 禁改写强制校验 | 引用悬空拒绝创建；专业改写平台本体拦截 | doc/顶层架构 §1 |
| N6 | 技能包挂载位（基础骨架 + 部门技能包） | A-3 结论 | doc/顶层架构 §6.1 |
| N7 | 工具库 + 工具工厂编译器（编译式固化） | 生成→沙箱→三关→审批→版本化 | doc/顶层架构 §7 |

开放歧义：**A-2 基础数字人能否带 RAG**（影响分类轴严谨性）——待定。

### 9.2 旧产品化设计稿中未做的项（DESIGN.md §11 + DESIGN_MVP §0）

| ID | 事项 | 说明 |
|---|---|---|
| U1 | 定时任务/调度器（APScheduler） | 「持续调研（⑥）」依赖此能力，**当前未做** |
| U2 | 分时用模 R9（白天本地/夜间公司） | 时间窗路由设计已定，未实现 |
| U3 | 扫描件 PDF OCR | 检测空文本层标记 needs_ocr，OCR 明确不做 |
| U4 | 断电重连增强 | 仅实现轻量：checkpoint + 启动续跑；无心跳看门狗 |
| U5 | 前端安装向导（硬探测/自动选型/一键装 Ollama） | 本地模式仅做了简化：bge-m3 embedding，未做 4B/8B LLM 自动选型向导 |
| U6 | 本地 4B/8B LLM 接入（仅评估未落地为主对话） | 现状本地通道仅用于 benchmark A/B 基线 |

### 9.3 反射（reflection）层的完整形态

reflection 六元已定义（独立、不共享、只回流自身 Spec），但**反思-回流-重编译**闭环仅在文档与评审中，代码尚未提供 reflection 复盘入口与 Spec 回流装配。

### 9.4 部分落地的已知缺口

- **exact-calculator MCP**：只有定义 JSON（input_schema），无实现代码（未启动）。
- **编排-调研并写 demo 页面**（通路库未命中 → 数字人设计编排图 → 动态建数字人 → 工具自生长）属概念验证设计，未见全自动入口。
- **前端上传「＋」按钮**：暂未开放（提示占位）。

### 9.5 截断续生成（truncation recovery）—— 已实现（2026-09-09，commit 91a02c4）

> 落地详情：llm.py `_looks_truncated` + `chat_with_continuation`；capability 写码/
> reviewer 通道接入；context_mgr.refine_system_block 预留。同时修复
> host.docker.internal 误走 LLM_PROXY 的 502 回归（netutil.is_local_url 覆盖
> *.internal）。实测 qwen extract_code 由「三轮写崩」恢复为 R1 一轮 pass。

**背景**（2026-09-09 能力题/4 元消融实证）：qwen2.5:7b 等小模型在长上下文修正时输出会
中途截断（现象：writer 修正代码从 857 字符塌缩到 61~73 字符、引号/三引号未闭合即
SyntaxError、沙箱报 `unterminated string literal`）。V4-Flash 无此现象，但任何模型在
极长输出/高并发下都可能被服务端 `max_tokens` 截断或连接中断截断。

**目标机制**：LLM 回复被截断时，不把它当最终结果（也不整题重来），而是自动「压缩上下文 →
从断点续写 → 拼接完整」重试，直到产出完整回复或达次数上限。

**截断检测**（三层，纯代码判定，不用 LLM 自评）：
1. `finish_reason == "length"`（OpenAI 流式末 chunk 的标准信号 = 输出撞 max_tokens 上限）；
2. 启发式语法中断：回复以未闭合的 `"""` / `"` / `'` / `(` / `[` / `{` / `\` 结尾（对应沙箱
   `unterminated ... literal` 类错误）；
3. 已知模型输出上限检查：回复字符数 ≈ 模型 max_tokens × 4 且无自然结束符。

**续写策略**：不是重发原 prompt（会导致重复/遗忘已写内容），而是：
- 保留已生成内容的尾部 N 字符（如 800）作为「续写锚点」；
- 上下文压缩：把 system 里的本体段/历史反馈压缩（context_mgr.refine_handoff 同源），
  腾出输出预算；
- 新请求 user = 锚点 + 「这是你上次输出被截断的末尾，请从该点继续写完整剩余内容，
  不要重复锚点之前的内容」；
- 拼接锚点前内容 + 本次续写 → 再次检测 → 循环（≤3 次）。

```python
# ---------- 截断续生成（pseudo-code, 拟落 backend/app/llm.py + capability.py） ----------
MAX_CONTINUE = 3          # 单轮内最多续写次数
ANCHOR_TAIL = 800         # 续写锚点：保留已生成内容的尾部字符数

def _looks_truncated(reply: str, finish_reason: str | None) -> bool:
    """截断判定：finish_reason==length（权威） + 启发式语法中断（兜底）。"""
    if finish_reason == "length":
        return True
    tail = (reply or "").rstrip()
    if not tail:
        return True
    # 启发式：以未闭合的成对符号/引号结尾 => 极可能被硬截断
    for sym in ('"""', "'''", '"', "'", "(", "[", "{", "\\"):
        if tail.endswith(sym):
            return True
    return False

def _continue_prompt(reply: str, anchor_chars: int) -> list[dict]:
    """续写请求：锚点 + 续写指令（不重发原任务，避免重复生成）。"""
    anchor = reply[-anchor_chars:] if len(reply) > anchor_chars else reply
    return [{"role": "user", "content":
             f"以下是你上次输出被截断的末尾：\n```\n{anchor}\n```\n"
             "请从该点**继续**把内容写完整（不要重复锚点及之前的内容，"
             "不要解释，直接续写剩余部分）。"}]

def generate_with_truncation_recovery(messages, *, max_tokens_hint) -> tuple[str, dict]:
    """主生成入口：正常调用 → 若截断则压缩上下文 + 断点续写 → 拼接完整。

    返回 (完整回复, {truncated: bool, continues: int})。
    设计要点：续写前用 context_mgr 压缩 system/历史（本体段按 kind 预算精炼），
    保证续写请求落在模型上下文窗口内；续写自身也可能被截断 → 循环续写。
    """
    reply = _chat_once(messages, expect_finish_reason=True)   # -> (text, finish_reason)
    truncated = _looks_truncated(reply.text, reply.finish_reason)
    continues = 0
    while truncated and continues < MAX_CONTINUE:
        compact = context_mgr.refine_system_block(messages, keep_budget=...)
        # 续写（同一会话消息栈：append 而非 replace，模型能看到自己上文）
        cont = _chat_continue(compact, _continue_prompt(reply.text, ANCHOR_TAIL))
        reply.text += cont.text                                  # 拼接
        continues += 1
        truncated = _looks_truncated(cont.text, cont.finish_reason)
    if truncated:
        return reply.text, {"truncated": True, "continues": continues}
    return reply.text, {"truncated": False, "continues": continues}

# 调用方（capability._solve_with_ontology / chat._generate / pipeline._run_nominate）：
#   text, meta = generate_with_truncation_recovery(...)
#   if meta["truncated"]:      # 重试 N 次仍截断 → 才算 fail，带标记供上层记录
#        return {..., "truncated": True}
```

**落点**（待实现，不在本仓库当前代码中）：
- `backend/app/llm.py`：加 `_looks_truncated` + `generate_with_truncation_recovery`（`_iter_openai`
  已能取到 usage chunk 的 `finish_reason`）；在 `_call_llm_with_usage` 的 retry 循环之上包一层。
- `context_mgr.py`：加 `refine_system_block(messages, budget)`（把 system 里的本体段按
  KIND_INPUT_BUDGET 精炼，供续写前压缩）。
- 接入点：capability（能力题写码修正轮）、chat._generate（长对话）、pipeline._run_nominate。

### 9.6 本轮新增设计（2026-09-10 设计 · 2026-09-11 起逐项落地）

| 章节 | 事项 | 状态 | 需求 | 指标 |
|---|---|---|---|---|
| §10 | **统一消息协议 DMP** | **部分实现（2026-09-11）**：协议层 `protocol.py` 落地 + JSON 解析器收敛（5 套→1 套）；业务链路切换（P2/P3）未做 | R-13 | T-H |
| §11 | **内容类型体系** | **已实现（2026-09-11）** | R-14 | T-I |
| §12 | **RAG 摄取数字人「知识摄取官」** | **已实现（2026-09-11）**：seed 落地（4 锚点 + 15 本体 + 4 动作 + owns 关系） | R-15 | — |
| §13 | **数字人工作台** | **已实现（2026-09-11）**：列表—详情五 Tab + 创建向导三来源 + 全局任务抽屉 + **状态收敛**（`useWorkbench.ts`，组件内 0 个 useState）+ **按需加载** | R-16 | T-J |

> **建议落地顺序**：§11（数据模型，风险最低、收益直接）→ §13（纯前端，可独立交付）→ §12（依赖 §11 词表落地）→ §10（协议层，影响面最大，按四阶段灰度）。

---

## 10. 统一消息协议（DMP · Digital-native Message Protocol）—— 部分实现（2026-09-11）

> **背景（2026-09-10）**：当前「与大语言模型的一切交互」**没有统一抽象**——
> **入参**是裸 `list[dict]` + 自由文本：system 由 `chat._system_prompt`（chat.py:205）把身份/锚点/本体/关系/RAG **拍平成 `- 名称：定义` 中文行**，历史行是 DB 的 `{role, content}` 直灌，用户输入是裸字符串；
> **出参**是混合态：数字人回复=自由 markdown，而工具/抽取/提名/判卷/设计=JSON；
> 且 **JSON 抠取有 4 套互不相同的实现**（`llm.extract_json`(llm.py:561) / `pipeline._extract_json`(pipeline.py:660) / `main._design_changes_via_llm`(main.py:1776) / `main._design_pipeline_via_llm`(main.py:1846)），
> **provider 分发逻辑重复 3 份**（`chat._dispatch`(chat.py:506) / `capability._call_channel`(capability.py:242) / `llm.stream_pick`(llm.py:517)）。
>
> 本章把这四条收敛为**一个协议层**：**所有聊天（对话 / 能力题 / 本体抽取 / 装配分诊 / pipeline 设计 / 训练师归因 / 考核）一律用 JSON 在 LLM 中传递**，**入参与出参双向统一**。

### 10.1 设计约束（硬边界，不可破）

| # | 约束 | 说明 |
|---|---|---|
| C1 | **OpenAI 兼容外壳不可破** | DeepSeek-V4-Flash / GLM 5.2 / Ollama 三家都只接受 `messages: [{role, content: str}]`，**`content` 必须是字符串** → JSON 必须序列化为字符串（`ensure_ascii=False`），不能改用 content blocks |
| C2 | **LLM 无终审权（铁律 L1）** | 协议只负责「结构化传输」；任何字段落库前仍走确定性校验（闭集 / 类型 / span） |
| C3 | **流式体验不可退** | `/api/chat/stream` 逐 token 渲染 markdown 是核心体验 → 需要「增量 JSON 解码」，不能等完整 JSON 到了才显示 |
| C4 | **旧会话可读** | 已落库的 `chat_messages.content` 是纯文本 → 必须无损回放（legacy 适配器） |
| C5 | **可关断** | 协议层必须能一键回退自由文本（`DMP_MODE=off`），协议 bug 不得阻断主链 |

### 10.2 消息信封（Envelope）—— 唯一数据模型

新增模块 `backend/app/protocol.py`：

```python
@dataclass
class Envelope:
    v: int                        # 协议版本（当前 1）
    kind: str                     # 闭集，见下表
    from_: dict                   # {"type":"user|identity|system", "id":int|None, "name":str}
    to: dict | None               # {"type":"identity|system", "id":int|None, "name":str}
    payload: dict                 # kind 专属字段（见下表）
    task: dict | None = None      # 机读任务契约 {"id","goal","criterion"}（协作/接缝专用）
    refs: list[dict] = field(default_factory=list)   # 证据引用 [{"kind":"chunk","id":12,"span":[0,40]}]
    meta: dict = field(default_factory=dict)         # {"session_id","turn","proto","ts"}
```

**kind 闭集与 payload**（与现状一一对应，保证可平移）：

| kind | 方向 | payload 关键字段 | 现状对应 |
|---|---|---|---|
| `instruction` | system → llm | `identity{name,mission,description}`、`rules[]`、`guardians[]` | `_system_prompt` 头部 + 「回答规则（铁律）」段 |
| `context` | system → llm | `anchors[]`、`ontology[]`、`relations[]`、`actions[]`、`rag[]`（**每条带 ref**） | `_system_prompt` 的四个【锚点/本体段/关系约束/参考资料】段 |
| `question` | user → llm | `text`、`attachments[]` | 用户原始输入字符串 |
| `answer` | llm → user | `text`（markdown）、`citations[]`、`refused: bool` | 数字人回复 |
| `tool_call` | llm → system | `call`、`args`、`id` | `<tool_call>{...}</tool_call>`（`chat._parse_tool_call`） |
| `tool_result` | system → llm | `id`、`ok`、`result`、`error` | 「动作「X」执行结果：{json}」（chat.py:983） |
| `review_request` | identity → identity | `task`、`artifact`、`criterion` | pipeline review 边（对齐上轮定的 `{"call":"review","task":…}`） |
| `review_result` | identity → identity | `verdict`、`issues[]`、`patch?` | reviewer 诊断（capability._review_diagnose） |
| `verdict` | llm → system | `label`、`reason`、`refs[]` | 判卷 / 考核 / 装配分诊 |
| `handoff` | identity → identity | `kind`、`content`、`budget` | context_mgr 交接物 |

**渲染与解析（唯二拍平点）**：

```python
def render(env: Envelope) -> dict:
    """Envelope → OpenAI 消息。content 为 JSON 字符串（C1）。"""
    return {"role": _role(env), "content": json.dumps(asdict(env), ensure_ascii=False)}

def parse(content: str) -> Envelope:
    """LLM 输出 / DB 行 → Envelope；非 JSON 或 schema 不符 → legacy 包装（C4）。"""
    # 失败：Envelope(kind="answer", payload={"text": content}, meta={"legacy": True})
```

### 10.3 入参打包：system 从「巨型中文文本」变为「结构化上下文」

| 旧（现状） | 新（DMP） |
|---|---|
| 1 条巨型 system，本体/关系/RAG 拍平成 `- 名称：定义` 文本行 | 1 条 `instruction` + 1 条 `context`，本体条目**保留 `id/kind/name/definition/tags` 原字段** |
| 历史 DB 行直接回灌 `{role, content}` | 历史行存 `envelope` JSONB，回灌即 `render` |
| 用户输入 = 裸字符串 | `question` envelope |
| 工具结果 `json.dumps` 塞进 user 文本 | `tool_result` envelope（独立消息，语义清晰） |

**收益（为什么值得做）**：

1. **证据可校验（铁律 L3 前置）**：`context.ontology` / `context.rag` 每条必须带 `ref`（candidate_id / chunk_id），代码可在发送前确定性校验「引用不悬空」。
2. **数字人间路由可机读**：上轮定的「数字人之间用 JSON 通信、路由只读机读字段」天然落在这里——`task` + `to` 即路由输入，`definition` 文本只作人读/LLM 自觉层。
3. **可观测**：`context.sent` 回传前端时结构完整，前端可按「本体 / RAG / 规则」分区展示，而非一坨文本。
4. **可测试**：协议层是纯函数（无 LLM 依赖），可做到 100% 单测覆盖。

### 10.4 出参统一：单一解析器 + 结构化输出约束

**统一入口** `llm.structured()`：

```python
def structured(messages: list[dict], *, schema: str, channel: str = "llm",
               temperature: float = 0.0, retry: int = 1) -> dict:
    """结构化生成：JSON 模式 → 协议解析 → schema 校验 → 重试 / 降级。"""
```

| provider | JSON 模式 | 强制手段 |
|---|---|---|
| DeepSeek-V4-Flash（`llm`） | ✅ | `response_format={"type":"json_object"}` |
| GLM 5.2（`llm2`） | ✅ | 同上 |
| Ollama（`ollama`，qwen2.5） | ✅ | `format="json"` |

**三套私有解析器全部退役**，收敛到 `protocol.parse()`；`llm.extract_json` 保留为 `parse` 的薄封装（不破坏既有调用方）。

**校验失败策略**（分链，严格对齐 R-1.4 / R-1.5）：

- **降级链**（抽取 / 摘要 / 概念兜底）：重试 1 次 → 仍失败则该字段回落**规则兜底**（不中断主链）。
- **终审链**（判卷 / 考核 / 装配分诊）：重试 1 次 → 仍失败 → **显式失败**，禁止静默兜底。

### 10.5 全链路时序（协议视角）

<svg viewBox="0 0 680 330" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="DMP 协议时序">
<rect x="0" y="0" width="680" height="330" fill="#FFFFFF"/>
<defs><marker id="dmpa" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="24" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">DMP：入参打包 → 渲染 → 调用 → 解析 → 回传（唯一协议层）</text>
<g font-size="11">
<rect x="40" y="38" width="110" height="30" rx="6" fill="#E6F1FB" stroke="#185FA5" stroke-width="1"/><text x="95" y="57" text-anchor="middle" font-weight="600" fill="#0C447C">前端</text>
<rect x="180" y="38" width="130" height="30" rx="6" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/><text x="245" y="57" text-anchor="middle" font-weight="600" fill="#3C3489">chat / capability</text>
<rect x="340" y="38" width="130" height="30" rx="6" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/><text x="405" y="57" text-anchor="middle" font-weight="600" fill="#085041">protocol.py</text>
<rect x="500" y="38" width="140" height="30" rx="6" fill="#FAEEDA" stroke="#854F0B" stroke-width="1"/><text x="570" y="57" text-anchor="middle" font-weight="600" fill="#633806">LLM 通道 llm/llm2/ollama</text>
</g>
<g stroke="#B4B2A9" stroke-width="1" stroke-dasharray="3 3">
<path d="M95 68 V318"/><path d="M245 68 V318"/><path d="M405 68 V318"/><path d="M570 68 V318"/>
</g>
<g font-size="10.5">
<path d="M95 92 H240" fill="none" stroke="#185FA5" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="100" y="88" fill="#0C447C">① question{"text":"…"}</text>
<path d="M245 122 H400" fill="none" stroke="#534AB7" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="250" y="118" fill="#3C3489">② pack_context(identity/anchors/ontology/relations/rag) + pack_history</text>
<path d="M405 152 H250" fill="none" stroke="#534AB7" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="256" y="148" fill="#3C3489">③ instruction + context envelope（本体条目带 ref）</text>
<path d="M245 182 H565" fill="none" stroke="#0F6E56" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="250" y="178" fill="#085041">④ dispatch(channel).messages = [render(env) …]（content = JSON 串）</text>
<path d="M570 212 H250" fill="none" stroke="#854F0B" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="256" y="208" fill="#633806">⑤ token 流（JSON 文本增量）</text>
<path d="M245 242 H400" fill="none" stroke="#0F6E56" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="250" y="238" fill="#085041">⑥ StreamJsonReader / parse() + schema 校验</text>
<path d="M405 272 H250" fill="none" stroke="#534AB7" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="256" y="268" fill="#3C3489">⑦ Envelope{answer, citations, refused}</text>
<path d="M245 302 H100" fill="none" stroke="#185FA5" stroke-width="1.4" marker-end="url(#dmpa)"/><text x="105" y="298" fill="#0C447C">⑧ SSE delta → done（或整包 JSON）</text>
</g>
</svg>

### 10.6 流式 + JSON：增量字段解码（StreamJsonReader）

流式端点若直接吐 JSON，前端无法逐 token 渲染。解法是**增量字段解码**，而非放弃流式：

```
StreamJsonReader（protocol.py）
  逐 token 喂入 → 惰性扫描 "payload" → "text" → 字符串值 → 产出 delta
  结束时对完整文本做一次 json.loads + schema 校验
  校验失败 → meta.degraded=True，前端降级为 raw 文本渲染（内容不丢）
```

前端 `streamChat` 新增 `onDelta(text)` 回调（与现有 `onToken` 签名兼容），**渲染逻辑不变**（仍是增量 markdown），因此 C3 不受损。

### 10.7 provider 分发收敛（去重 3 份）

```python
llm.dispatch(channel, messages, *, temperature=..., stream=False, ollama_model=None)
```

`chat._dispatch` / `capability._call_channel` / `llm.stream_pick` 三处重复实现统一为其薄封装；调用方只传 `channel`（`llm` / `llm2` / `ollama`），与 provider/model 解析解耦。

### 10.8 落地路线（四阶段，每阶段独立可回滚）

| 阶段 | 内容 | 回滚手段 |
|---|---|---|
| **P1 协议层** | 新增 `protocol.py` + 单测（纯函数，无 LLM）；`chat_messages` 加 `envelope JSONB` 迁移列 | 无副作用（尚未接入） |
| **P2 对话切换** | `chat._generate` / `_system_prompt` 改 envelope；**双写** envelope + content | `DMP_MODE=off` 回退文本 |
| **P3 其它链路** | capability / ontology / assembly / pipeline / trainer / benchmark 切 `llm.structured` | 逐链路开关 |
| **P4 收尾** | 退役 3 套私有 JSON 解析器；`content` 列转只读 | — |

### 10.9 已知风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| token 膨胀 | JSON 键名本身耗 token，长 system 更贵 | 短键 + 仅在必要处展开；`context` 支持分块裁剪 |
| 小模型 JSON 不稳 | qwen2.5:7b 长 JSON 易截断 | 复用 §9.5 截断续生成；schema 校验失败即降级 |
| 流式解码复杂度 | 增量渲染出错风险 | `StreamJsonReader` 带单测；失败降级 raw 文本 |
| 协议翻车阻断主链 | 全站不可用 | `DMP_MODE=off` 一键回退 + 四阶段分步上线 |

**状态**：**部分实现（2026-09-11）** —— P1 协议层 + P4 解析器收敛已完成，P2/P3 业务链路切换未做。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P1 协议层** | 新增 `backend/app/protocol.py`：`Envelope`（kind 十类闭集）+ `render`/`parse`（唯二拍平点）+ `parse_json`（唯一抠取实现）+ `StreamJsonReader`（增量字段解码）+ `pack_instruction/context/question/answer/tool_result` + `dmp_enabled()`（C5 开关） | ✅ |
| **P4 解析器收敛** | **5 套 → 1 套**（比设计预估的 4 套多一套：`mcp.py` 里还有一份）：`llm.extract_json` 改为薄封装；`pipeline._extract_json` 删除；`main._design_changes_via_llm` / `_design_pipeline_via_llm` 的手写「剥围栏 + 截取」替换为 `protocol.parse_json`；`mcp._extract_json` 改为薄封装 | ✅ |
| P2 对话切换 | `chat._generate` / `_system_prompt` 改 envelope（双写 `chat_messages.envelope`） | ❌ 未做 |
| P3 其它链路 | capability / ontology / assembly / trainer / benchmark 切 `llm.structured()` + `response_format=json_object` | ❌ 未做 |

验证：`backend/scripts/verify_protocol.py` —— 信封往返（C1）、legacy 兼容（C4）、`parse_json` 五类输入、`StreamJsonReader` 增量解码（C3）、`DMP_MODE` 开关（C5）、kind 闭集，**容器内 10/10 通过**。

> **踩坑（已修）**：`StreamJsonReader.feed` 原先在目标字段解完后（`done=True`）直接丢弃后续 token，导致缓冲里 JSON 不完整、`finish()` 永远返回 None（复验 9/10）。修复：done 之后**继续累积缓冲**但不产出 delta。

需求 R-13；指标 test-metrics §T-H。

---

## 11. 内容类型体系（chunk type · 词表 + 两维度）—— 已实现（2026-09-11）

> **背景（2026-09-10）**：`chunks` 表现有 9 列（`id/doc_id/seq/text/summary/tags/embedding/content_hash/source_meta`），**没有任何"这条知识是什么性质"的字段**。
> 用户要求：分块时给 chunk 加 `type`，该 type 与**置信度**和**是否必须绝对遵守**两个维度相关，且 **type 词表可由用户自定义**。

### 11.1 两个维度是正交的（为什么不能合成一个字段）

| 维度 | 回答的问题 | 取值 | 谁定 |
|---|---|---|---|
| **置信度 confidence** | 这条知识**有多准** | `high` / `medium` / `low` | 词表默认值（type 决定），用户可覆盖 |
| **强制等级 mandatory** | 这条知识**能不能违反** | `0` 参考 / `1` 建议 / `2` 强制 | 同上 |

正交性举例（证明必须两个维度）：

- **低置信 + 强制**：本部门未公开的内部口径——不一定可靠，但必须遵守；
- **高置信 + 参考**：公开的行业案例——很准确，但仅供参考。

> ⚠️ 与既有「领域标签」的关系：`tags`（candidates 的 `TAG_PRESET` 6 类闭集，ontology.py:36）描述「**属于哪个业务域**」，`type` 描述「**这条知识是什么性质、约束力多强**」。两者**正交，不可互相替代**。

### 11.2 词表：`chunk_types` 表（用户可自定义）

```sql
CREATE TABLE IF NOT EXISTS chunk_types (
    id                 BIGSERIAL PRIMARY KEY,
    code               TEXT NOT NULL UNIQUE,              -- 机读键（英文）
    label              TEXT NOT NULL,                     -- 显示名（中文）
    description        TEXT,                              -- 判定说明（会进 LLM prompt）
    default_confidence TEXT    NOT NULL DEFAULT 'medium', -- high|medium|low
    default_mandatory  SMALLINT NOT NULL DEFAULT 0,       -- 0 参考 | 1 建议 | 2 强制
    priority           INT     NOT NULL DEFAULT 0,        -- 检索/注入排序权重
    builtin            BOOLEAN NOT NULL DEFAULT FALSE,    -- 预设项：可改可停用，不可删
    status             TEXT    NOT NULL DEFAULT 'active', -- active|disabled
    created_at         DOUBLE PRECISION NOT NULL
);
```

**预设词表**（`builtin=true`，首启用幂等 upsert 写入）：

| code | label | 默认置信度 | 默认强制 | 权重 | description（进 prompt） |
|---|---|---|---|---|---|
| `hard_rule` | 强制规则 | high | **2** | 100 | 平台/部门硬约束，回答必须遵守，违反即错 |
| `regulation` | 法规条文 | high | **2** | 95 | 法律法规、标准条款，逐字遵守 |
| `fact` | 事实陈述 | high | 1 | 60 | 可验证的客观事实 |
| `metric` | 数据指标 | medium | 1 | 55 | 数字/口径定义，须标注来源与时点 |
| `term` | 术语概念 | high | 0 | 50 | 名词定义与解释 |
| `case` | 案例示例 | medium | 0 | 40 | 具体实例，仅供参考 |
| `opinion` | 观点建议 | low | 0 | 30 | 主观建议，可被推翻 |
| `unknown` | 未定类型 | low | 0 | 0 | **兜底**：判定失败或不在词表（**不可删**） |

<svg viewBox="0 0 680 350" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="chunk type 两维度矩阵与判定流">
<rect x="0" y="0" width="680" height="350" fill="#FFFFFF"/>
<defs><marker id="cta" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="24" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">type = 词表项 → 派生（置信度 × 强制等级），两维度正交</text>
<g font-size="11">
<text x="70" y="66" text-anchor="middle" font-weight="600" fill="#444441">置信度</text>
<text x="180" y="76" text-anchor="middle" font-weight="600" fill="#633806">参考 (0)</text>
<text x="290" y="76" text-anchor="middle" font-weight="600" fill="#633806">建议 (1)</text>
<text x="400" y="76" text-anchor="middle" font-weight="600" fill="#633806">强制 (2)</text>
<text x="70" y="112" text-anchor="middle" font-weight="600" fill="#085041">高</text>
<text x="70" y="160" text-anchor="middle" font-weight="600" fill="#854F0B">中</text>
<text x="70" y="208" text-anchor="middle" font-weight="600" fill="#7A2E2E">低</text>
<rect x="130" y="90" width="100" height="40" rx="6" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="180" y="115" text-anchor="middle" fill="#085041">term 术语概念</text>
<rect x="240" y="90" width="100" height="40" rx="6" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="290" y="115" text-anchor="middle" fill="#085041">fact 事实陈述</text>
<rect x="350" y="90" width="100" height="40" rx="6" fill="#FCEBEB" stroke="#F09595" stroke-width="1"/><text x="400" y="109" text-anchor="middle" fill="#7A2E2E">regulation 法规</text><text x="400" y="123" text-anchor="middle" fill="#7A2E2E">hard_rule 强制规则</text>
<rect x="130" y="138" width="100" height="40" rx="6" fill="#F6F6F4" stroke="#D3D1C7" stroke-width="1"/><text x="180" y="163" text-anchor="middle" fill="#5F5E5A">—</text>
<rect x="240" y="138" width="100" height="40" rx="6" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="290" y="163" text-anchor="middle" fill="#854F0B">metric 数据指标</text>
<rect x="350" y="138" width="100" height="40" rx="6" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="400" y="163" text-anchor="middle" fill="#854F0B">case 案例示例</text>
<rect x="130" y="186" width="100" height="40" rx="6" fill="#F6F6F4" stroke="#D3D1C7" stroke-width="1"/><text x="180" y="211" text-anchor="middle" fill="#5F5E5A">opinion 观点建议</text>
<rect x="240" y="186" width="100" height="40" rx="6" fill="#F6F6F4" stroke="#D3D1C7" stroke-width="1"/><text x="290" y="211" text-anchor="middle" fill="#5F5E5A">—</text>
<rect x="350" y="186" width="100" height="40" rx="6" fill="#F6F6F4" stroke="#D3D1C7" stroke-width="1"/><text x="400" y="211" text-anchor="middle" fill="#5F5E5A">—</text>
<text x="470" y="163" font-size="10.5" fill="#5F5E5A">低置信 + 强制 = 内部口径（必须遵守但不确定）</text>
<text x="470" y="179" font-size="10.5" fill="#5F5E5A">高置信 + 参考 = 公开案例（准确但仅供参考）</text>
</g>
<rect x="24" y="244" width="632" height="90" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="264" text-anchor="middle" font-size="12" font-weight="600" fill="#444441">判定链（LLM 只提名 type，两维度由词表确定性裁决 —— 铁律 L1）</text>
<g font-size="10.5">
<rect x="40" y="276" width="120" height="44" rx="6" fill="#EEEDFE" stroke="#AFA9EC" stroke-width="1"/><text x="100" y="294" text-anchor="middle" fill="#3C3489">① LLM 提名 type</text><text x="100" y="310" text-anchor="middle" fill="#534AB7">{"type":"hard_rule"}</text>
<path d="M160 298 H176" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#cta)"/>
<rect x="178" y="276" width="140" height="44" rx="6" fill="#FAEEDA" stroke="#FAC775" stroke-width="1"/><text x="248" y="294" text-anchor="middle" fill="#633806">② 闭集校验</text><text x="248" y="310" text-anchor="middle" fill="#854F0B">不在词表 → unknown</text>
<path d="M318 298 H334" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#cta)"/>
<rect x="336" y="276" width="160" height="44" rx="6" fill="#E1F5EE" stroke="#9FE1CB" stroke-width="1"/><text x="416" y="294" text-anchor="middle" fill="#085041">③ 词表查默认两维度</text><text x="416" y="310" text-anchor="middle" fill="#0F6E56">落 type_confidence/mandatory</text>
<path d="M496 298 H512" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#cta)"/>
<rect x="514" y="276" width="126" height="44" rx="6" fill="#E6F1FB" stroke="#85B7EB" stroke-width="1"/><text x="577" y="294" text-anchor="middle" fill="#0C447C">④ 用户可覆盖</text><text x="577" y="310" text-anchor="middle" fill="#185FA5">type_source=user</text>
</g>
</svg>

### 11.3 chunk 新增字段（迁移走 idempotent ALTER）

```sql
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS type            TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS type_confidence TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS type_mandatory  SMALLINT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS type_source     TEXT DEFAULT 'llm';  -- llm|rule|user|legacy
CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(type);
```

均为标量列 → `backup.py` 的 `JSONB_COLS` / `ARRAY_COLS` 映射**无需新增**。

### 11.4 判定链：LLM 只提名 type，两维度由词表裁决

1. `llm.summarize_chunk()` 扩展为返回 `{"summary","tags","type"}`，prompt 内**给出当前 active 词表（code + label + description）**并要求 `严格按 JSON 输出`；
2. **闭集校验**：`type ∉ active 词表` → 置 `unknown`（**不臆造**，对齐铁律 L2）；
3. **两维度裁决**：`type_confidence` / `type_mandatory` **一律取词表默认值**——保证同一 type 在全库语义一致；LLM 若额外提名 confidence 只作记录，**不直接落库**（提名-裁决分离）；
4. **用户可覆盖**：UI 单条改 type → 两维度重取词表默认；手动改两维度 → `type_source='user'`；
5. **LLM 不可用**（降级链）：`rule_type(text)` 关键词规则 → type → 词表默认值，`type_source='rule'`。

### 11.5 检索与注入：mandatory 从「提示词自觉」升级为「数据属性」

- `ingest.search(query, top_k, tag, type, min_confidence, mandatory)` 增加三组过滤；
- **对话注入策略**：
  - `mandatory=2` 的命中 → **必选注入**（不参与 top-k 竞争，按 `priority` 降序，上限 N 条），在 context envelope 里进入独立 `rules` 分区，并在 instruction 里声明「以下为强制约束」；
  - `mandatory<=1` 的命中 → 走原 top-k，进入 `rag` 分区。
- 这条把铁律 L2「不知道就说不知道」与 L3「绝对准确」从**提示词约定**升级为**数据驱动**：强制类知识是 chunk 的数据属性，不依赖模型自觉。

### 11.6 前端（RagPage 扩展）

- chunk 明细表新增 **type 列**（徽章配色由 mandatory 决定：2 红 / 1 橙 / 0 灰）+ 筛选 chips（type / confidence / mandatory）；
- chunk 详情卡显示两维度 + `type_source`；
- 新增「**类型词表**」管理面板：增删改（`builtin` 与 `unknown` 不可删）、默认两维度、停用、排序权重；
- 单 chunk 手动改 type（用户终审）。

### 11.7 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/rag/types` | 词表列表（含 builtin / status） |
| POST | `/api/rag/types` | 新增自定义 type（code 唯一校验） |
| PUT | `/api/rag/types/{id}` | 改 label / description / 两维度 / priority / status |
| DELETE | `/api/rag/types/{id}` | 删除（**builtin/unknown 或被 chunk 引用 → 拒绝**） |
| POST | `/api/rag/chunks/{id}/type` | 单 chunk 终审改 type |

`GET /api/rag/chunks`、`GET /api/rag/chunks/{id}`、`/api/rag/search` 增加 type 三维输出（search 增加过滤参数）。

**状态**：**已实现（2026-09-11）**。

| 落地位置 | 内容 |
|---|---|
| `backend/app/chunk_types.py`（新模块） | 8 条 builtin 词表 + `ensure_seed` / CRUD / `resolve`（提名→裁决）/ `rule_type`（规则兜底）/ `catalog_for_prompt` |
| `backend/app/db.py` | `chunk_types` 表 + `chunks` 四列（新库建表即含）+ idempotent ALTER 迁移 + `idx_chunks_type` + `_ensure_schema` 内置 seed |
| `backend/app/llm.py` | `summarize_chunk(text, type_catalog)` 返回 `summary/tags/type/source`（**只提名 type**） |
| `backend/app/ingest.py` | 写路径（`_ingest_one_file` / `run_summary_repair`）裁决落库；读路径（`list_chunks` / `get_chunk` / `search`）三维过滤与输出 |
| `backend/app/research.py` | 调研入库按来源等级映射置信度，`type='fact'`、`mandatory=0` |
| `backend/app/chat.py` | `_rag_snippets` 分两路（`rules` 必选 / `texts` top-K）；`_system_prompt` 新增【强制约束】段与对应铁律 |
| `backend/app/main.py` | `GET/POST/PUT/DELETE /api/rag/types`、`POST /api/rag/chunks/{id}/type`、search/chunks 三维参数 |
| `client/src/pages/RagPage.tsx` | type 徽章列（配色由 mandatory 决定）+ 三维筛选 chips + 类型词表管理面板 + 单条终审改类型 |
| `backend/scripts/verify_chunk_types*.py` | 两个验证脚本（DDL/词表/裁决 + 数据流四步），容器内运行 |

> **踩坑记录**：`CREATE INDEX ... ON chunks(type)` **不能**写在 `_SCHEMA_SQL` 里 —— 老库上 `CREATE TABLE IF NOT EXISTS chunks` 会被跳过，索引语句随即引用尚未 ALTER 出来的列，`_init_schema` 直接抛 `column "type" does not exist` 导致后端启动失败（2026-09-11 实测）。索引创建必须在迁移块里、ALTER 之后。

需求 R-14；指标 test-metrics §T-I。

---

## 12. RAG 摄取数字人「知识摄取官」—— 已实现（2026-09-11）

> **背景（2026-09-10）**：当前**没有**任何负责知识摄取的数字人——`ingest.py`（加载/分段/摘要/嵌入）与 `ontology.py`（候选提名→三关）都是 `kind='ingest' | 'ontology'` 的**确定性 job**；现有 7 个 digital人全是研发角色（需求/设计/代码/审查/测试/调试/训练师）。
> 用户要求：**加入一个"提取 RAG 的数字人"，并把 chunk type 体系等内容作为本体加入其中**。

### 12.1 定位与不可逾越的边界（先说权责）

**「知识摄取官」（code: `knowledge_ingestor`，category=`general` 平台级通用）**：把「文档 → 可检索知识」的**摄取契约人格化**——让分块规则、类型判定规则、去重与覆盖契约、证据规则成为**可审批、可对话、可复盘**的本体。

> ⚠️ **它不执行摄取**。执行仍是确定性 `ingest` job（铁律 L1：代码定路径、LLM 只做节点；LLM 无终审权）。
> 数字人只在**三个位置**介入（见 §12.4）：① 规则定义（本体审批）② 类型词表维护 ③ 复盘提名。

### 12.2 六元组定义

| 元 | 内容 | 落库 |
|---|---|---|
| **identity** | name=知识摄取官；mission=「把部门文档切成分段、判明性质、守住证据，产出可检索、可溯源、可审批的知识」；description=平台级通用数字人；prompt 尾=取向声明 | `identities`（category=`general`，reactive=off） |
| **ontology** | 摄取契约本体（见 §12.3，15 条：8 概念 + 7 规则） | `persona_ontology` + `relations` |
| **actions** | `rag_ingest`（触发摄取 job）/ `chunk_classify`（单 chunk 重判 type）/ `chunk_search`（检索预览）/ `ontology_extract`（触发本体抽取） | `persona_actions`（builtin 4 条） |
| **guardians** | type 闭集校验、mandatory ∈ {0,1,2}、span 逐字命中、去重键唯一、覆盖须用户确认 | 确定性代码（**无 LLM 终审权**） |
| **interface** | `POST /api/rag/upload-files` 语义面 + pipeline 摄取节点接缝 + `research.ingest_results` | 代码约定 |
| **reflection** | 复盘「被判错类型的 chunk」→ 提名**词表/规则修订**（走审批，**禁热更**） | 见 §9.3（闭环未落地，此处先占位） |

### 12.3 本体条目清单（seed 内容，用户审批后生效）

**概念（kind=`概念`，8 条）**

| name | definition |
|---|---|
| Chunk 分段单元 | 文档切分后的最小检索单位；句边界优先、超长句硬切并保留重叠（默认 800 字 / 重叠 100） |
| 分段摘要 | 对 chunk 生成的语义浓缩；**是 embedding 的向量化对象**，非原文 |
| 内容类型 type | `chunk_types` 词表项；派生「置信度 + 强制等级」两个正交维度 |
| 置信度 | 知识可信程度：high / medium / low；由 type 词表默认值决定，用户可覆盖 |
| 强制等级 | 知识约束力：0 参考 / 1 建议 / 2 强制；=2 时对话必选注入 |
| 证据 span | chunk 内**逐字可回溯**的字符区间（start/end）；本体候选必须携带 |
| 去重键 | 文档 `name`：hash 相同则跳过；同名不同 hash 视为冲突 |
| 入库契约 | 覆盖语义：`DELETE chunks`（mentions 级联）+ `UPDATE documents`（保 id）+ `INSERT` 新 chunks |

**规则（kind=`规则`，7 条）**

| name | definition |
|---|---|
| 分块规则 | 句边界优先切分；单句超长硬切且相邻块保留重叠，禁止整篇成一块 |
| 类型判定规则 | LLM **只提名 type**；两维度一律由词表默认值**确定性裁决**，提名不直接落库 |
| 未知类型不臆造 | type 不在 active 词表 → 置 `unknown`，禁止临时造类型 |
| 证据逐字规则 | 抽取的实体名称与定义必须在来源 chunk 内逐字命中，否则拒绝入池 |
| embedding 对象规则 | 只对 `summary` 向量化（bge-m3 锁定），原文与 tags 不参与向量 |
| 强制注入规则 | `mandatory=2` 的 chunk 必选注入上下文，**不参与 top-k 截断** |
| 覆盖确认规则 | 同名不同 hash **不静默覆盖**，必须 dry-run 呈现并二次确认 |

**关系（`relations`）**：`知识摄取官 --owns--> 入库契约 / 类型判定规则 / 强制注入规则 / 证据逐字规则`；`内容类型 type --派生--> 置信度 / 强制等级`；`Chunk 分段单元 --produces--> 分段摘要`。

### 12.4 权责边界（关键：不让数字人越权）

<svg viewBox="0 0 680 300" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="摄取数字人与确定性链路的权责边界">
<rect x="0" y="0" width="680" height="300" fill="#FFFFFF"/>
<defs><marker id="kia" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="24" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">知识摄取官（规则与复盘）⟂ 确定性 ingest job（执行）</text>
<rect x="24" y="42" width="632" height="96" rx="10" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="62" text-anchor="middle" font-size="12" font-weight="600" fill="#444441">确定性摄取链路（job，0 LLM 主干，数字人不介入逐条判定）</text>
<g font-size="10.5">
<rect x="40" y="74" width="110" height="48" rx="6" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="95" y="94" text-anchor="middle" fill="#0C447C">① 加载+分段</text><text x="95" y="110" text-anchor="middle" fill="#185FA5">800/100 重叠</text>
<path d="M150 98 H166" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#kia)"/>
<rect x="168" y="74" width="130" height="48" rx="6" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="233" y="94" text-anchor="middle" fill="#0C447C">② summary + type 提名</text><text x="233" y="110" text-anchor="middle" fill="#185FA5">词表裁决两维度</text>
<path d="M298 98 H314" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#kia)"/>
<rect x="316" y="74" width="120" height="48" rx="6" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="376" y="94" text-anchor="middle" fill="#0C447C">③ embedding</text><text x="376" y="110" text-anchor="middle" fill="#185FA5">仅 summary</text>
<path d="M436 98 H452" fill="none" stroke="#5F5E5A" stroke-width="1.4" marker-end="url(#kia)"/>
<rect x="454" y="74" width="110" height="48" rx="6" fill="#FFFFFF" stroke="#85B7EB" stroke-width="1"/><text x="509" y="94" text-anchor="middle" fill="#0C447C">④ 入库 PG</text><text x="509" y="110" text-anchor="middle" fill="#185FA5">chunk 原子事务</text>
<rect x="574" y="74" width="66" height="48" rx="6" fill="#FFF8E6" stroke="#FAC775" stroke-width="1"/><text x="607" y="94" text-anchor="middle" fill="#633806">⑤ 三关</text><text x="607" y="110" text-anchor="middle" fill="#854F0B">+ 审批</text>
</g>
<path d="M340 138 V156" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#kia)"/>
<rect x="24" y="158" width="632" height="126" rx="10" fill="#EEEDFE" stroke="#534AB7" stroke-width="1"/>
<text x="340" y="178" text-anchor="middle" font-size="12" font-weight="600" fill="#3C3489">知识摄取官 —— 只在三个位置介入（本体可审批 / 可对话 / 可复盘）</text>
<g font-size="10.5">
<rect x="40" y="190" width="194" height="80" rx="6" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="137" y="208" text-anchor="middle" font-weight="600" fill="#3C3489">① 规则定义（本体）</text><text x="137" y="226" text-anchor="middle" fill="#534AB7">15 条契约本体经装配入池</text><text x="137" y="240" text-anchor="middle" fill="#534AB7">改规则 = 改本体 + 重新审批</text><text x="137" y="258" text-anchor="middle" fill="#534AB7">禁止热更（禁绕过审批生效）</text>
<rect x="243" y="190" width="194" height="80" rx="6" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="340" y="208" text-anchor="middle" font-weight="600" fill="#3C3489">② 类型词表维护</text><text x="340" y="226" text-anchor="middle" fill="#534AB7">用户增删改 chunk_types</text><text x="340" y="240" text-anchor="middle" fill="#534AB7">改默认两维度 / 权重 / 停用</text><text x="340" y="258" text-anchor="middle" fill="#534AB7">builtin 与 unknown 不可删</text>
<rect x="446" y="190" width="194" height="80" rx="6" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="1"/><text x="543" y="208" text-anchor="middle" font-weight="600" fill="#3C3489">③ 复盘提名（reflection）</text><text x="543" y="226" text-anchor="middle" fill="#534AB7">统计 unknown / 低置信占比</text><text x="543" y="240" text-anchor="middle" fill="#534AB7">提名词表或规则修订</text><text x="543" y="258" text-anchor="middle" fill="#534AB7">→ 用户审批 → 重编译（§9.3）</text>
</g>
</svg>

### 12.5 落地方式

| 项 | 方案 |
|---|---|
| seed 脚本 | 新增 `backend/seed_ingest_identity.py`：幂等 upsert（按 name 定位 identity；persona_ontology 按 `(identity_id, kind, name)` 唯一键）；写入 15 条本体 + 4 条 action + 关系 |
| 定位与去重 | 数字人以 `identities.name='知识摄取官'` 为幂等键；本体条目以 `name_norm` 去重（复用 `ontology._norm_name`） |
| 与 ingest 链路的耦合 | **零耦合**：`ingest.py` 不 import 数字人；摄取规则由 `chunk_types` 表 + 常量实现，数字人是这些规则的**可审批封装**（避免"加数字人 = 主链引入 LLM 依赖"） |
| 前端 | 出现在「数字人工作台」列表（§13），可对话问「分块规则是什么」并得到本体约束内的回答 |
| 复盘入口 | 复用 §9.3 reflection 设计；一期先做**只读统计卡片**（unknown 占比、type 分布、来源构成） |

**状态**：**已实现（2026-09-11）**。

| 落地位置 | 内容 |
|---|---|
| `backend/seed_ingest_identity.py`（新） | 幂等 seed：创建数字人「知识摄取官」(id=8, category=general, approved) + 4 条锚点 + 15 条本体（8 概念 / 7 规则）+ 4 条 builtin 动作 + 沉淀到 `candidates` 并挂 5 条 `owns` 关系 |
| 运行方式 | `docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend python seed_ingest_identity.py`（可重复跑，不重复写） |
| 零耦合保证 | `ingest.py` **不 import** 该数字人；摄取规则仍由 `chunk_types` 表 + 常量实现，数字人只是规则的**可审批封装**（避免"加数字人 = 主链引入 LLM 依赖"） |
| 一期边界 | 复盘（reflection）只做规则定义与词表维护；「复盘提名」统计卡片待 §9.3 闭环落地后接入（工作台「复盘」Tab 已占位） |

需求 R-15。

---

## 13. 数字人工作台（IdentityWorkbench）—— 主体已实现（2026-09-11）

> **背景（2026-09-10）**：当前存在**两个割裂的页面**——
> ① 导航里名为「**数字人创建台**」的 tab（App.tsx:329，`id='ingest'`）实际内容是**一键流水线 + 任务进度 + 事件流**（App.tsx:360-429）；
> ② 真正的数字人管理中心 `IdentityPanel.tsx`（807 行）却**嵌在「本体图谱」tab 里**（OntologyPage.tsx:684）。
> 用户要求：**把「数字人创建台」和数字人管理中心整合成一个页面**，并解决「不好看 + 逻辑不顺」。

### 13.1 现状问题清单（重构依据）

| # | 问题 | 证据 |
|---|---|---|
| P1 | **Tab 名与内容错位** | 「数字人创建台」不创建数字人（是摄取流水线）；管理中心藏在本体图谱里 |
| P2 | **三块名不副实** | 块 3 标题「创造·删除·修改」，实际只有"创建"（删除/修改在每张卡片头部） |
| P3 | **创建路径被拆到两处** | 页面内有创建弹窗；"从图谱种子创建"在本体图谱的 seedMode，只能靠一段文字指路 |
| P4 | **两个入口同一后端行为** | "提名备选"与"创建弹窗留空"都调 `nominateIdentities()`，用户无法理解 |
| P5 | **状态高度分散** | 23 个 `useState` + 4 个 `Record<id,…>` + 4 个独立 `useEffect` + bench 5s 轮询；开着卡片数据可能还没拉回 |
| P6 | **完成一件事点击过多** | 「装配本体并生效」最少 5 步，且要手工等 job 再回来看，无完成回调 |
| P7 | **单卡承载过多职责** | 一张卡同时是身份编辑 / 锚点 / 本体库 / 装配 / A-B 测试 / 版本回滚 / MCP 绑定，默认全收起→用户不知道里面有内容 |
| P8 | **审批语义不直观** | pending 与 rejected 混排同一网格；"拒绝"实为**硬删除**（main.py:885），UI 未预警 |
| P9 | **锚点编辑是裸输入框** | 无 label、type 为自由文本，与 `ENTITY_TYPES` 闭集脱节 |
| P10 | **下一步引导弱** | 空态只有一句 note，无 CTA 流转（入库→提名→审批→装配） |

### 13.2 目标：单页「数字人工作台」+ 导航调整

| 项 | 旧 | 新 |
|---|---|---|
| 导航项 | `ingest` = 「数字人创建台」（流水线/任务/事件） | `identity` = 「**数字人**」（工作台，含列表/详情/创建/知识装配/测试/任务抽屉） |
| 管理中心 | 嵌在 `ontology` tab 顶部 | **并入工作台**；`ontology` tab 回归**纯图谱 + 候选治理** |
| 任务与事件 | 独占一个 tab 的整页 | **全局抽屉**（任何页面可开，快捷键 `⌘/Ctrl+J`） |
| 页面总数 | 8（conversation / ingest / rag / ontology / pipeline / chat / mcp / settings） | 8（conversation / **identity** / rag / ontology / pipeline / chat / mcp / settings） |

**设计原则**：① 列表—详情（master-detail）替代"平铺三块"；② 一切**按需加载**（打开才拉）；③ 流程**内联成时间线**（不再跨区块跳）；④ 状态**单一 store**。

### 13.3 页面线框（深色主题，与 styles.css `:root` 变量一致）

<svg viewBox="0 0 680 432" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="数字人工作台线框">
<rect x="0" y="0" width="680" height="432" fill="#14161a"/>
<text x="340" y="18" text-anchor="middle" font-size="13" font-weight="600" fill="#d6d3cb">数字人工作台（IdentityWorkbench）· 单页三区：列表 / 详情 Tab / 任务抽屉</text>
<rect x="0" y="26" width="680" height="44" fill="#1d2026" stroke="#32363e" stroke-width="1"/>
<text x="14" y="53" font-size="11" font-weight="600" fill="#d6d3cb">rag-mvp</text>
<g font-size="9.5">
<rect x="86" y="36" width="42" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="107" y="50" text-anchor="middle" fill="#8b8f98">对话</text>
<rect x="132" y="36" width="46" height="20" rx="5" fill="#4f83cc" stroke="#4f83cc" stroke-width="1"/><text x="155" y="50" text-anchor="middle" fill="#ffffff">数字人</text>
<rect x="182" y="36" width="36" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="200" y="50" text-anchor="middle" fill="#8b8f98">RAG</text>
<rect x="222" y="36" width="46" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="245" y="50" text-anchor="middle" fill="#8b8f98">本体</text>
<rect x="272" y="36" width="36" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="290" y="50" text-anchor="middle" fill="#8b8f98">编排</text>
<rect x="312" y="36" width="36" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="330" y="50" text-anchor="middle" fill="#8b8f98">测试</text>
<rect x="352" y="36" width="36" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="370" y="50" text-anchor="middle" fill="#8b8f98">MCP</text>
<rect x="392" y="36" width="36" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="410" y="50" text-anchor="middle" fill="#8b8f98">设置</text>
</g>
<circle cx="620" cy="46" r="4" fill="#4caf78"/><text x="630" y="50" font-size="9" fill="#8b8f98">事件流</text>
<g font-size="9.5">
<rect x="14" y="80" width="152" height="36" rx="6" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="26" y="94" fill="#8b8f98">数字人总数</text><text x="26" y="109" fill="#d6d3cb" font-size="13">8</text>
<rect x="174" y="80" width="152" height="36" rx="6" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="186" y="94" fill="#8b8f98">已批准启用</text><text x="186" y="109" fill="#4caf78" font-size="13">7</text>
<rect x="334" y="80" width="152" height="36" rx="6" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="346" y="94" fill="#8b8f98">待审备选 / 待审本体</text><text x="346" y="109" fill="#d98324" font-size="13">3 / 12</text>
<rect x="494" y="80" width="172" height="36" rx="6" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="506" y="94" fill="#8b8f98">运行中 job</text><text x="506" y="109" fill="#a277e0" font-size="13">1 · 本体装配 42%</text>
</g>
<rect x="14" y="126" width="180" height="264" rx="8" fill="#1d2026" stroke="#32363e" stroke-width="1"/>
<text x="26" y="144" font-size="10.5" font-weight="600" fill="#d6d3cb">数字人列表</text>
<rect x="26" y="152" width="156" height="22" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="34" y="167" font-size="9" fill="#8b8f98">搜索 / 筛选：全部·通用·领域专家</text>
<g font-size="9">
<rect x="26" y="180" width="156" height="44" rx="6" fill="#23272f" stroke="#4f83cc" stroke-width="1.2"/><text x="34" y="195" fill="#d6d3cb">知识摄取官</text><text x="34" y="208" fill="#8b8f98">通用 · 4 actions</text><rect x="140" y="186" width="36" height="13" rx="4" fill="#4caf78"/><text x="158" y="196" text-anchor="middle" font-size="8" fill="#0b1a12">已批准</text>
<rect x="26" y="228" width="156" height="44" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="34" y="243" fill="#d6d3cb">代码工程师</text><text x="34" y="256" fill="#8b8f98">领域专家 · 6 actions</text><rect x="140" y="234" width="36" height="13" rx="4" fill="#4caf78"/><text x="158" y="244" text-anchor="middle" font-size="8" fill="#0b1a12">已批准</text>
<rect x="26" y="276" width="156" height="44" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="34" y="291" fill="#d6d3cb">测试工程师</text><text x="34" y="304" fill="#8b8f98">领域专家 · reactive</text><rect x="140" y="282" width="36" height="13" rx="4" fill="#4caf78"/><text x="158" y="292" text-anchor="middle" font-size="8" fill="#0b1a12">已批准</text>
<rect x="26" y="324" width="156" height="44" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="34" y="339" fill="#d6d3cb">需求分析师</text><text x="34" y="352" fill="#8b8f98">待审批备选</text><rect x="140" y="330" width="36" height="13" rx="4" fill="#d98324"/><text x="158" y="340" text-anchor="middle" font-size="8" fill="#1a1206">待审</text>
</g>
<rect x="204" y="126" width="462" height="264" rx="8" fill="#1d2026" stroke="#32363e" stroke-width="1"/>
<text x="216" y="145" font-size="11" font-weight="600" fill="#d6d3cb">知识摄取官</text>
<rect x="292" y="134" width="34" height="14" rx="4" fill="#4caf78"/><text x="309" y="144" text-anchor="middle" font-size="8" fill="#0b1a12">已批准</text>
<rect x="331" y="134" width="44" height="14" rx="4" fill="#a277e0"/><text x="353" y="144" text-anchor="middle" font-size="8" fill="#150d24">通用</text>
<rect x="544" y="133" width="52" height="16" rx="5" fill="#4f83cc"/><text x="570" y="145" text-anchor="middle" font-size="8.5" fill="#ffffff">对话</text>
<rect x="602" y="133" width="52" height="16" rx="5" fill="#23272f" stroke="#d06060" stroke-width="1"/><text x="628" y="145" text-anchor="middle" font-size="8.5" fill="#d06060">删除</text>
<g font-size="9">
<rect x="216" y="158" width="52" height="20" rx="5" fill="#4f83cc"/><text x="242" y="172" text-anchor="middle" fill="#ffffff">概览</text>
<rect x="272" y="158" width="78" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="311" y="172" text-anchor="middle" fill="#8b8f98">知识与本体</text>
<rect x="354" y="158" width="78" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="393" y="172" text-anchor="middle" fill="#8b8f98">能力与工具</text>
<rect x="436" y="158" width="86" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="479" y="172" text-anchor="middle" fill="#8b8f98">测试与版本</text>
<rect x="526" y="158" width="52" height="20" rx="5" fill="#23272f" stroke="#32363e" stroke-width="1"/><text x="552" y="172" text-anchor="middle" fill="#8b8f98">复盘</text>
</g>
<rect x="216" y="188" width="438" height="88" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/>
<text x="228" y="205" font-size="9.5" font-weight="600" fill="#d6d3cb">身份（identity）</text>
<rect x="228" y="212" width="130" height="20" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="236" y="226" font-size="8.5" fill="#8b8f98">name 知识摄取官</text>
<rect x="366" y="212" width="80" height="20" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="374" y="226" font-size="8.5" fill="#8b8f98">category</text>
<rect x="454" y="212" width="60" height="20" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="462" y="226" font-size="8.5" fill="#8b8f98">reactive</text>
<rect x="522" y="212" width="120" height="20" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="530" y="226" font-size="8.5" fill="#8b8f98">mission</text>
<rect x="228" y="240" width="404" height="26" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="236" y="257" font-size="8.5" fill="#8b8f98">prompt 附加指令（可编辑）</text>
<rect x="216" y="286" width="212" height="92" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/>
<text x="228" y="303" font-size="9.5" font-weight="600" fill="#d6d3cb">锚点（固定注入）· 5</text>
<g font-size="8.5" fill="#8b8f98">
<rect x="228" y="310" width="188" height="16" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="236" y="322">入库契约 · 规则</text>
<rect x="228" y="330" width="188" height="16" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="236" y="342">类型判定规则 · 规则</text>
<rect x="228" y="350" width="188" height="16" rx="4" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="236" y="362">Chunk 分段单元 · 概念</text>
</g>
<rect x="436" y="286" width="218" height="92" rx="6" fill="#23272f" stroke="#32363e" stroke-width="1"/>
<text x="448" y="303" font-size="9.5" font-weight="600" fill="#d6d3cb">reactive 反思开关</text>
<rect x="448" y="312" width="34" height="16" rx="8" fill="#32363e"/><circle cx="457" cy="320" r="6" fill="#8b8f98"/><text x="490" y="324" font-size="8.5" fill="#8b8f98">关闭（未启用复盘回流）</text>
<rect x="448" y="336" width="194" height="32" rx="5" fill="#1d2026" stroke="#32363e" stroke-width="1"/><text x="456" y="350" font-size="8.5" fill="#8b8f98">能力题往返：最近 3 次 pass 4/6 · 5/6 · 5/6</text><text x="456" y="362" font-size="8.5" fill="#8b8f98">测试失败会自动提名补本体</text>
<rect x="14" y="398" width="652" height="26" rx="6" fill="#1d2026" stroke="#a277e0" stroke-width="1"/>
<text x="26" y="415" font-size="9.5" fill="#a277e0">任务与事件抽屉（全局 · ⌘/Ctrl+J）</text>
<text x="300" y="415" font-size="9" fill="#8b8f98">本体装配  42%  ·  scan 空闲  ·  repair 空闲  ·  ontology 空闲</text>
<text x="600" y="415" font-size="9" fill="#4caf78">实时</text>
</svg>

### 13.4 详情五 Tab（替代原 5 个折叠块 + 备选块）

| Tab | 内容 | 数据来源（API） |
|---|---|---|
| **① 概览** | identity 编辑（name/mission/description/category/reactive/prompt）+ 关键词 + 锚点增改批拒 + 身份考核 | `identities` / `anchors/*` / `exam` |
| **② 知识与本体** | 本体库（persona_ontology）+ **装配时间线**（选源→运行→待审清单→一键终审/丢弃/恢复） | `persona-ontology` / `assemble` / `assembly` / `assembly/confirm·discard·restore` |
| **③ 能力与工具** | actions 列表 + 提名 + MCP 绑定/解绑（schema 预览） | `actions/*` / `identities/{id}/mcp` / `bind-mcp` / `available` |
| **④ 测试与版本** | 四臂 benchmark + 归因提名 + merge/v+1 + 回滚（含变更拒收） | `benchmark/*` |
| **⑤ 复盘** | reflection 统计卡（type 分布 / unknown 占比 / 来源构成）+ 修订提名（一期只读） | 新增（§9.3/§12.5） |

**待审队列**单独成区（不再是"备选块"）：pending 与 rejected **分列**，且「拒绝」按钮改为「拒绝并删除」，二次确认文案明确**不可恢复**（修 P8）。

### 13.5 状态设计：一个 store 替代 23 个 useState

```ts
useWorkbench()  // hooks/useWorkbench.ts —— useReducer 单一 store
state = {
  list: Identity[],          // 列表（轻量，进入页面拉一次）
  filter: { cat, status, q },
  selectedId: number | null,
  detail: Record<number, IdentityDetail>,   // 按需加载：打开才拉（替代 4 个 useEffect + 5s 轮询）
  wizard: { open, step, draft },
  jobDrawer: { open },
}
```

- **按需加载**：列表 → 轻量字段；打开某数字人才拉 detail（本体/装配/bench/MCP）。
- **事件驱动刷新**：监听 `/ws/events` 的 `ontology`/`assembly`/`benchmark` job 完成事件 → 失效对应 detail 缓存（**替代 5s 轮询**，修 P5/P6）。
- **派生值用 `useMemo`**：filtered list / 分组 / 徽章，不再各存一份 state。

### 13.6 创建向导（3 步，统一两个入口 → 修 P3/P4）

```
＋ 新建数字人
 ├─ ① 来源：○ 自主提名（读语料高频词提名，原"提名备选"）
 │          ○ 从图谱种子（选 candidates → 创建，原 OntologyPage seedMode）
 │          ○ 空白模板（手填身份，可选预装通用本体）
 ├─ ② 身份：name / mission / description / category(general|domain_expert) / reactive / prompt
 └─ ③ 装配策略：○ 立即自动装配（创建后跑 assembly job） ○ 稍后手动
        → 提交后跳转新数字人「知识与本体」Tab，装配进度内联显示
```

「提名备选」不再是独立按钮，而是向导的第 ① 步选项 → 消除 P4 的"两个入口同一行为"困惑。

### 13.7 关键流程：5 步装配 → 1 条时间线（修 P6）

| 旧（5 步，跨区块，需手工等） | 新（1 条内联时间线） |
|---|---|
| 展开「装配本体」→ 点「装配更多本体」→ 切走等 job → 回来点开清单 → 点「一键终审装配」 | 知识与本体 Tab 内：**选源 → 运行（进度内联）→ 待审清单（按 action 分组，可 dismiss）→ 一键终审/丢弃/恢复**；job 完成由 WS 事件自动刷新清单并高亮提示 |

### 13.8 组件与文件规划

```
client/src/pages/IdentityWorkbench.tsx          页面壳（三区布局 + 全局抽屉）
client/src/pages/IdentityPanel.tsx              → 退役（逻辑迁入以下组件）
client/src/components/identity/IdentityList.tsx      左列表
client/src/components/identity/DetailHeader.tsx      详情头（含 对话/删除）
client/src/components/identity/tabs/OverviewTab.tsx
client/src/components/identity/tabs/KnowledgeTab.tsx   （anchors + persona_ontology + 装配时间线）
client/src/components/identity/tabs/CapabilityTab.tsx  （actions + MCP）
client/src/components/identity/tabs/TestingTab.tsx     （benchmark + 版本）
client/src/components/identity/tabs/ReflectionTab.tsx  （一期只读）
client/src/components/identity/CreateWizard.tsx
client/src/components/identity/JobDrawer.tsx           （任务进度 + 事件流，从 ingest tab 迁入）
client/src/hooks/useWorkbench.ts
```

- `App.tsx`：tab `ingest` → `identity`（label「数字人」）；`OntologyPage.tsx` **移除内嵌 IdentityPanel**（回归纯图谱）。
- 原 ingest tab 的「一键创建数字人」流水线卡片 → 由 **CreateWizard + JobDrawer** 承接（语义一致：一键 = 向导第 ① 步选"自主提名" + 第 ③ 步选"立即装配"）。
- 复用现有 **26 个 API**（无需后端改动），新增 §11.7 的 chunk_types 系列（属 RAG 页面）。

### 13.9 迁移与验收

| 阶段 | 内容 |
|---|---|
| M1 | 新增 `useWorkbench` + `IdentityWorkbench` 壳 + 列表/详情骨架（只读） |
| M2 | 迁 5 个 Tab（逐个替换 IdentityPanel 的 block 函数） |
| M3 | CreateWizard（三来源合一）+ JobDrawer 接管任务/事件 |
| M4 | OntologyPage 移除内嵌面板；`identity` tab 上线；删除旧代码 |

**验收指标**：test-metrics §T-J（J-1 创建步数 ≤3；J-2 装配步数 5→≤2 且无需手工等待；J-3 详情按需加载（打开才发请求）；J-4 `useState` 23→≤3；J-5 无 console 报错且 8 页可切换）。

**状态**：**主体已实现（2026-09-11）**。

| 需求 | 落地情况 |
|---|---|
| 列表—详情 + 五 Tab | ✅ `IdentityWorkbench.tsx`（由原 `IdentityPanel.tsx` 改造并重命名）：左列表（搜索 + 全部/已批准/待审筛选）+ 右详情（概览 / 知识与本体 / 能力与工具 / 测试与版本 / 复盘）；原「5 个折叠块平铺」与「备选块」已并入 |
| 导航调整 | ✅ `ingest` → `identity`（label「数字人」）；`OntologyPage` 移除内嵌面板，回归纯图谱 |
| 全局任务抽屉 | ✅ 原「数字人创建台」整页内容（数据流水线 / 任务进度 / 事件流）收进 `App` 层右侧抽屉，顶栏「任务」按钮（带运行中计数）随时可开 |
| 创建向导 | ✅ 两步：① 来源（自主提名 / 图谱种子 / 空白模板）② 身份表单；消除原「提名按钮」与「创建弹窗」两个入口语义重复 |
| 待审队列 | ✅ 合并为列表筛选「待审 / 已拒」；详情头支持就地批准；「删除」对 rejected 显示为「删除已拒」 |
| **状态收敛（J-4）** | ✅ **已完成（2026-09-11 补）**：新增 `client/src/hooks/useWorkbench.ts` 收纳全部 state / 派生值 / 加载副作用，**组件内 `useState` 归零** |
| **按需加载（J-3）** | ✅ **已完成（2026-09-11 补）**：只拉「当前选中」数字人的装配摘要 / 本体段 / 评测摘要（旧实现对全部已批准数字人并发拉取，O(N) 请求）；评测轮询同样只针对选中项 |

> **实现手法：「解构法」**——`useWorkbench(refreshKey)` 返回全部状态与 setter，组件
> `const {…} = useWorkbench(refreshKey)` 解构后**渲染代码一行未改**（变量名保持一致）。
> 这比"把 500 余行 handler 全搬进 hook"的风险低得多，且同样达成「组件内无 useState」。
>
> **剩余未做**：装配的**事件驱动刷新**（仍走 `refreshKey`）；「拒绝」的**二次确认弹窗**。
> J-1 / J-2 / J-5 / J-6 已满足。
>
> **踩坑 1**：「可用 MCP（勾选绑定）」面板错乱的根因不在本组件，而是全局 `input{width:100%}`
> 撑满了 checkbox —— 见 `styles.css` 的 `input[type="checkbox"]` 豁免规则。
> **踩坑 2**：抽 hook 后组件里残留了三个已被搬走的定义（`approvedAnchors`、availableMcp 的
> `useEffect`、`setAvailableMcp` 解构缺失），`tsc` 一次性全部报出（重复声明 / 未定义），
> 按提示逐个清理即可 —— 这也是"先构建后验证"的价值。

需求 R-16。

---

## 14. 设计变更流程（强制约定）

1. **先文档后代码（或同提交）**：任何架构/实体/流程/指标变更，先在 `docs/design.md` 落字再改代码；紧急 bug 修复允许代码先行，但必须在同一提交内或紧接提交中回写本文档对应段落。
2. **三文档联动**：需求变更 → `docs/requirement.md`（增/改 REQ 条目 + 状态）；实现变更 → `design.md`（改设计段）；指标/阈值变更 → `docs/test-metrics.md`。
3. **状态字段**：design 章节若只写了「待定」歧义，须在确认后回填结论并加日期。
4. **评审提交模板**：变更提交信息需能索引到本文档章节号（如 `feat(pipeline): 新增 X（design §5.3）`）。

---

## 15. DFMEA 自动编排链路（需求 → N 专家 + 1 DFMEA 工程师）—— 设计已定，未实现

> **编号说明**：本节为 2026-09-11 新增。§14「设计变更流程」是全文收尾约定，编号保持不变以免破坏已有引用，新设计统一从 §15 起追加。
>
> **场景**：用户提一句自然语言需求（部件 + 场景 + 标准），系统**自动生成**一条 DFMEA 流水线：由 **N 个部件专家数字人** + **1 个 DFMEA 工程师数字人**（+ 复核门）组成。DFMEA 工程师通过**查历史数据 / 查表 / 询问专家**得出潜在失效模式与 S/O/D/频次等数值；**凡 AI 臆想或全新功能的取值，必须标注为「AI 生成」**。

### 15.1 流水线拓扑

<svg viewBox="0 0 680 538" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="DFMEA 数字人流水线">
<rect x="0" y="0" width="680" height="538" fill="#FFFFFF"/>
<defs><marker id="d15" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="18" text-anchor="middle" font-size="15" font-weight="600" fill="#1F2328">需求 → DFMEA 数字人流水线（N 个专家 + 1 个 DFMEA 工程师）</text>
<rect x="200" y="32" width="280" height="40" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="52" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#444441">用户需求：一句自然语言（部件 + 场景 + 标准）</text>
<path d="M340 72 V84" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#d15)"/>
<rect x="180" y="88" width="320" height="52" rx="8" fill="#E1F5EE" stroke="#5DCAA5" stroke-width="1"/>
<text x="340" y="106" text-anchor="middle" dominant-baseline="central" font-size="13" fill="#085041">① 需求分析师 · 已有（id=1）</text>
<text x="340" y="124" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">输出：结构化需求规格 + 部件分解清单</text>
<path d="M340 140 V156" fill="none" stroke="#5F5E5A" stroke-width="1.5"/>
<path d="M130 156 H550 M130 156 V170 M340 156 V170 M550 156 V170" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#d15)"/>
<rect x="40" y="170" width="180" height="60" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="130" y="190" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">部件专家 A（结构）· 待新建</text>
<text x="130" y="212" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">材料 / 工况 / 边界</text>
<rect x="250" y="170" width="180" height="60" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="340" y="190" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">部件专家 B（电子）· 待新建</text>
<text x="340" y="212" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">常见失效 / 历史问题</text>
<rect x="460" y="170" width="180" height="60" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="550" y="190" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">部件专家 C（材料）… ×N</text>
<text x="550" y="212" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">按子系统可弹性扩展</text>
<path d="M130 230 V246 H340 V258 M340 230 V258 M550 230 V246 H340" fill="none" stroke="#5F5E5A" stroke-width="1.5"/>
<path d="M340 250 V258" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#d15)"/>
<path d="M196 254 V236" fill="none" stroke="#534AB7" stroke-width="1.2" stroke-dasharray="4 3" marker-end="url(#d15)"/>
<text x="204" y="245" dominant-baseline="central" font-size="11" fill="#534AB7">ask 回退（机制待补）</text>
<rect x="40" y="258" width="600" height="124" rx="10" fill="#F6F6F4" stroke="#B4B2A9" stroke-width="1"/>
<text x="56" y="276" dominant-baseline="central" font-size="12" font-weight="600" fill="#1F2328">② DFMEA 工程师 · 待新建（核心）— 逐字段按优先级取值，并标注每格来源</text>
<rect x="56" y="290" width="138" height="50" rx="8" fill="#FCEBEB" stroke="#F09595" stroke-width="1"/>
<text x="125" y="306" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#791F1F">1 历史 FMEA 库</text>
<text x="125" y="324" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#A32D2D">来源＝查表</text>
<rect x="204" y="290" width="138" height="50" rx="8" fill="#FCEBEB" stroke="#F09595" stroke-width="1"/>
<text x="273" y="306" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#791F1F">2 AP / S-O-D 表</text>
<text x="273" y="324" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#A32D2D">来源＝查表</text>
<rect x="352" y="290" width="138" height="50" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="421" y="306" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">3 询问专家数字人</text>
<text x="421" y="324" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">来源＝专家提供</text>
<rect x="500" y="290" width="138" height="50" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="569" y="306" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">4 AI 推断</text>
<text x="569" y="324" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">来源＝AI 生成·待确认</text>
<text x="56" y="360" dominant-baseline="central" font-size="11" fill="#5F5E5A">命中优先级 1 → 2 → 3 → 4：前一级有证据就不允许下一级代填；全新功能无任何证据 → 强制标注「AI 生成·待人工确认」</text>
<path d="M340 382 V394" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#d15)"/>
<rect x="180" y="398" width="320" height="46" rx="8" fill="#FAEEDA" stroke="#EF9F27" stroke-width="1"/>
<text x="340" y="414" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">③ DFMEA 复核员 · 待新建（review 门）</text>
<text x="340" y="432" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">核 S/O/D 依据是否充分；AI 生成项是否需人工确认</text>
<path d="M340 444 V456" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#d15)"/>
<rect x="140" y="460" width="400" height="48" rx="8" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1"/>
<text x="340" y="476" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#444441">DFMEA 报告：失效模式 / 后果 / S / 原因 / O / 控制 / D / AP</text>
<text x="340" y="494" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">每格带来源标注 + 待人工确认清单</text>
<rect x="40" y="516" width="600" height="18" rx="4" fill="#FFFFFF" stroke="#D3D1C7" stroke-width="1"/>
<text x="52" y="525" dominant-baseline="central" font-size="11" fill="#5F5E5A">绿＝已有资产　橙＝需新建（能力部分具备）　红＝完全缺失（数据与工具都没有）</text>
</svg>

### 15.2 取值优先级与来源标注（本链路的合规核心）

DFMEA 的每一格（失效模式 / 后果 / S / 原因 / O / 现有控制 / D / AP）都必须**先取值、再标来源**：

| 优先级 | 取值来源 | 标注 `source` | 前置条件 |
|---|---|---|---|
| 1 | 历史 FMEA 库命中（同部件/同类失效） | `history` | 需 §15.3-B1 数据域与 C1 查询动作 |
| 2 | AP 表 / S-O-D 评分准则查表 | `table` | 需 §15.3-B2 本体与 C2 查表动作 |
| 3 | 询问部件专家数字人（ask 回退） | `expert:<name>` | 需 §15.3-A2 专家数字人与 E1 ask 执行机制 |
| 4 | 无任何证据 → AI 推断 | `ai_inferred` | — |
| 4′ | **全新功能**（连类推依据都没有） | **`ai_new`（AI 生成·待人工确认）** | 必须进入人工确认清单 |

**硬约束（对齐三条铁律）**：

- **不得越级代填**：第 1 级有命中就不允许用第 2/3/4 级覆盖（L1 提名-裁决分离、L3 证据可引）。
- **`ai_new` 必须可被机器筛出**：复核门与前端据此生成「待人工确认清单」，不允许静默混入正常值。
- **来源随格存储**，不是整表一个标记 —— 同一张 DFMEA 表里不同格可以来源不同。

### 15.3 自动化缺口清单（回答「还差多少工具没生成」）

> 盘点基准：2026-09-11 代码库（`actions.py` 6 个 builtin、`mcp_imports/` 5 个 server / 13 个 tool、`capability_tools` 表仅 1 条编程题记录、本体与 RAG 中 **FMEA 相关零命中**）。

**A. 数字人资产（3 个）**

| # | 缺失项 | 现状 | 优先级 |
|---|---|---|---|
| A1 | **DFMEA 工程师**（核心，承载 §15.2 取值链） | 无；现有 7 个数字人全是软件研发角色 | **P0** |
| A2 | **部件专家数字人 × N**（结构/电子/材料/热…） | 无 | **P0** |
| A3 | **DFMEA 复核员**（review 门角色） | 无 | P1 |

**B. 数据与本体资产（4 类）**

| # | 缺失项 | 现状 | 优先级 |
|---|---|---|---|
| B1 | **历史 FMEA 库**（结构化：部件/失效模式/原因/O/D/措施） | **完全不存在**：无表、无导入、无样例数据 | **P0** |
| B2 | **AP 表 / S-O-D 评分准则**（AIAG-VDA 的 S/O/D 打分依据与 AP 行动优先级矩阵） | **完全不存在** | P1 |
| B3 | **失效模式库本体**（失效模式-原因-后果的分类树） | 无 | P1 |
| B4 | **部件知识本体**（材料/结构/工况/边界条件的领域概念） | 无 | P1 |

**C. 内置动作（5 个；`actions.py` 现仅 6 个通用动作）**

| # | 缺失动作 | 语义 | 优先级 |
|---|---|---|---|
| C1 | `fmea_history_query` | 按部件/关键词检索历史 FMEA 条目，返回带出处的候选值 | **P0** |
| C2 | `fmea_ap_table` | 按 (S, O, D) 查 AP 行动优先级；按字段与描述查评分准则 | P1 |
| C3 | `ask_expert` | 向指定专家数字人提问并取回结构化回答（**ask 回退的执行体**） | **P0** |
| C4 | `fmea_write_row` | 写一行 DFMEA 记录（含逐格 `source` 标注） | **P0** |
| C5 | `export_table` | 把 DFMEA 结果导出为 xlsx/csv | P2 |

**D. MCP / 外部服务（2 个）**

| # | 缺失项 | 现状 | 优先级 |
|---|---|---|---|
| D1 | `fmea-history` MCP（历史 FMEA 检索服务） | 现有 5 个 MCP 无任何 FMEA 能力；`exact-calculator`/`docx-accessor`/`ppt-read-write`/`bing-baidu-search`/`paper-crawler` 均不适用 | P1（若 B1 直接做成 PG 表 + C1 动作，可省） |
| D2 | Excel 写出能力 | **完全没有**：`loaders.py` 只读 xlsx（openpyxl 只读），前端无任何导出代码 | P2 |

**E. Pipeline 机制（6 处；「自动创建」能否成立的关键）**

| # | 缺失机制 | 现状（已核实） | 优先级 |
|---|---|---|---|
| E1 | **ask 回退可执行** | 图上画蓝虚线，引擎**完全不执行**（`ask` 在校验环检测/拓扑/数据流中被排除，pipeline.py:251/427/504） | **P0** |
| E2 | **review 门可执行** | 前端画红菱形，运行期**无门控**（`RELATION_REVIEW` 只在定义处出现） | P1 |
| E3 | **handoff 契约校验** | `handoff_schema` 是**死字段**（仅建表/CRUD，运行期零引用）；无「上游产出 kind 匹配下游入站 kind」校验 | P1 |
| E4 | **FMEA 专用 kind** | `context_mgr` 9 类 kind 中无「失效模式清单/评分取值/DFMEA 表」，只能落 generic；`ROLE_INPUT_KINDS` 不含 DFMEA 角色（走全收分支） | P1 |
| E5 | **自动挑选数字人（语义化）** | 现为 LLM 凭 `id/name/category` 三元组**硬选** persona_id（pipeline.py:682-687），无语义检索、失败即整体报错 | P2 |
| E6 | **生成时自动绑定 persona_id** | 主 UI 路径（`_design_pipeline_via_llm`）产出的节点 **persona_id 全为 None** → 能 approve 但运行时每个节点「未绑定数字人，跳过」＝空转 | **P0** |

**F. 前端（1 个）**

| # | 缺失项 | 现状 | 优先级 |
|---|---|---|---|
| F1 | **DFMEA 表格视图**（逐格来源高亮 + `ai_new` 待确认清单 + 人工覆盖） | 无 | P1 |

**合计 21 项**。按用户口径拆两层 —— **工具/机制类 13 项**（C 动作 5 + D 服务 2 + E 机制 6），**内容资产类 8 项**（A 数字人 3 + B 数据本体 4 + F 前端视图 1）。

### 15.4 「自动创建」现在能走到哪一步（现状评估）

| 环节 | 现状 | 结论 |
|---|---|---|
| 一句话 → 生成 pipeline **图** | **已实现**：`POST /api/pipeline/generate` → `pipeline.generate_from_request`（pipeline.py:676-753），单次 LLM 调用即产出节点+关系并落库 draft，且**自动选 persona_id** | ✅ 可用 |
| 一句话 → 生成**可运行**的 pipeline | 主 UI 路径（`POST /api/pipelines` 带 description）产出的节点 persona_id 全为 None（main.py:1989）→ approve 能过但运行空转 | ⚠️ 半可用 |
| 生成**语义化**关系（supply / ask / review 真正生效） | 关系类型闭集存在，但**运行期只有 ask 被特判排除、其余五类等价**；review 不门控、ask 不执行、`handoff_schema` 不校验 | ❌ 缺失 |
| 生成 **DFMEA 专用** pipeline | 无 DFMEA/部件专家数字人、无 FMEA kind、无对应动作 → 即使生成了图也**无人可绑、无数据可查** | ❌ 缺失 |

**结论**：**「生成流程图」这一步已经具备（且能自动挑数字人）；缺的是让图「活起来」的 13 项工具/机制，外加 8 项内容资产。** 其中 E1（ask 回退执行）、E6（生成即绑定）、A1/A2（DFMEA + 部件专家数字人）、B1（历史 FMEA 库）、C1/C3/C4（查表/问专家/写行三个动作）为 **P0 阻塞项** —— 不补则链路无法产出任何真实数值。

### 15.5 落地顺序建议

1. **P0-1 数据域与动作**：建历史 FMEA 表（B1）+ 三个 P0 动作（C1 `fmea_history_query` / C3 `ask_expert` / C4 `fmea_write_row`）。
2. **P0-2 机制**：E1（ask 回退执行）+ E6（生成时自动绑定 persona_id）。
3. **P0-3 数字人**：A2 部件专家 ×N → A1 DFMEA 工程师（其本体即 §15.2 取值优先级链与来源标注规则）。
4. **P1**：B2 AP/S-O-D 本体 + C2 查表动作 + E2 review 门控 + E4 FMEA kind + F1 前端表格视图。
5. **P2**：D2 Excel 导出 + E5 语义化选人 + C5/D1。

**状态**：设计已定（2026-09-11），**代码未实现**。需求编号 R-17。

---
*维护说明：本文档由 2026-09-09 代码库现状 + doc/ 历史设计（顶层架构、pipeline、DESIGN/DESIGN_MVP、编排示例）整理生成；所有标注「未实现」项以 §9 为准。*
*v1.1（2026-09-10）新增 §10 统一消息协议 / §11 内容类型体系 / §12 RAG 摄取数字人 / §13 数字人工作台，并新增 §9.6 未实现清单；原 §10 设计变更流程顺延为 §14。*
*v1.2（2026-09-11）§11 落地实现（chunk_types 词表 + 正交两维度 + mandatory 强制注入）；新增 §15 DFMEA 自动编排链路设计（含流程图与 21 项自动化缺口清单）。*
*注：仓库中**不存在** `backend/app/ontology/ontology.yaml`；本体的单一事实源是数据库（`candidates` / `persona_ontology` / `relations`）+ `backend/app/ontology.py` 内的常量闭集。*
