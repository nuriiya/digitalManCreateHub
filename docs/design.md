# 数字人全链路平台 · 系统设计（design.md）

> **文档版本**：v1.0（2026-09-09）· 状态：**对齐当前代码**
> **配套文档**：需求见 `docs/requirement.md`；测试指标见 `docs/test-metrics.md`（Test Metrics）。
> **维护约定（硬性）**：任何设计变更（架构/流程/实体/指标/入口）都必须**同步更新**本文件与 `docs/requirement.md`、`docs/test-metrics.md` 三份文档的对应条目；代码提交前先改文档，或在同一提交内完成（详见 §10）。

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
| RAG | documents、chunks、mentions、kv（元信息/embedding 锁定） |
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
| IdentityPanel（嵌入 OntologyPage） | 数字人管理中心：已有/备选/创造 + 装配 + 测试·版本 + 锚点 + **可调用 MCP 绑定** + reactive 开关 |
| RagPage | RAG 预览：文档库/chunk 明细/近邻检索/单 chunk 详情 |
| OntologyPage | 本体图谱（ReactFlow + dagre）+ 候选管理 + 二次编排终审门 + 角色子树聚焦 |
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

---

## 10. 设计变更流程（强制约定）

1. **先文档后代码（或同提交）**：任何架构/实体/流程/指标变更，先在 `docs/design.md` 落字再改代码；紧急 bug 修复允许代码先行，但必须在同一提交内或紧接提交中回写本文档对应段落。
2. **三文档联动**：需求变更 → `docs/requirement.md`（增/改 REQ 条目 + 状态）；实现变更 → `design.md`（改设计段）；指标/阈值变更 → `docs/test-metrics.md`。
3. **状态字段**：design 章节若只写了「待定」歧义，须在确认后回填结论并加日期。
4. **评审提交模板**：变更提交信息需能索引到本文档章节号（如 `feat(pipeline): 新增 X（design §5.3）`）。

---
*维护说明：本文档由 2026-09-09 代码库现状 + doc/ 历史设计（顶层架构、pipeline、DESIGN/DESIGN_MVP、编排示例）整理生成；所有标注「未实现」项以 §9 为准。*
