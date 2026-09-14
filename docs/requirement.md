# 数字人全链路平台 · 需求规格（requirement.md）

> **文档版本**：v1.2（2026-09-11）· **与 `docs/design.md` 一一对应**（每条标注 design 章节）。
> 状态标注：`已实现` / `部分实现` / `未实现` / `设计待定`。
> 测试验收指标见 `docs/test-metrics.md`。变更须遵守 design.md §10（三文档联动）。

## 需求-设计追溯速查

| Requirement 分组 | 对应 design § |
|---|---|
| R-1 安全与设计哲学 | design §1.2 / §1.1 |
| R-2 数字人领域模型 | design §2 |
| R-3 内容工程（文档→本体） | design §3.1–§3.3 |
| R-4 对话 | design §3.4 |
| R-5 测试与训练 | design §4 |
| R-6 MCP 与工具 | design §5.1–§5.2 |
| R-7 pipeline 编排 | design §5.3 |
| R-8 调研与知识入库 | design §5.4 |
| R-9 平台工程 | design §6 |
| R-10 前端 | design §7 |
| R-11 部署与运维 | design §8 |
| R-12 已设计未实现项 | design §9 |
| R-13 统一消息协议（DMP，入参+出参 JSON 化） | design §10 |
| R-14 内容类型体系（chunk type 词表 + 两维度） | design §11 |
| R-15 RAG 摄取数字人（知识摄取官） | design §12 |
| R-16 数字人工作台（创建台 + 管理中心合并） | design §13 |
| R-17 DFMEA 自动编排链路（需求→N 专家+DFMEA 工程师） | design §15 |

---

## R-1 安全与设计哲学（design §1）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-1.1 | LLM 输出一律为「提名」，确定性代码终审，用户唯一终审点 | 已实现 | 全链路无 LLM 直接落库；路由/判分/拓扑/校验 0 LLM |
| R-1.2 | 不知道就说不知道（铁律 L2） | 已实现 | 对话越界必拒答；benchmark 有 refused 类 |
| R-1.3 | 证据可引用（铁律 L3） | 已实现 | 候选带 mentions；检索回取原文 |
| R-1.4 | 判别/考核通道 LLM 失败不静默兜底 | 已实现 | exam 无判别器即失败；chat 抛 LLMError |
| R-1.5 | 提取/检索通道 LLM 失败可降级（不中断主链） | 已实现 | hash embedding / 规则 summary / 概念抽取失败返空 |
| R-1.6 | 降级链与终审链分离不混淆 | 已实现 | 见 R-1.4 / R-1.5 分界 |

## R-2 数字人领域模型（design §2）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-2.1 | 数字人 = 六元组封装体（identity/ontology/actions/guardians/interface/reflection） | 部分实现 | 六元中的 identity/ontology/actions/interface 已落地；guardians 代码固化；reflection 闭环未完成（见 R-12.3） |
| R-2.2 | 平台写死段 vs 部门填位段分离 | 部分实现 | identity 模板写死 + prompt 尾可配；ontology 由装配产出 |
| R-2.3 | 专业数字人创建路径：文档→RAG→本体→装配 | 已实现 | 见 R-3 各链 |
| R-2.4 | 基础数字人创建路径：Spec 编译（平台通用本体库） | 未实现 | design §9.1 N1–N7；A-2 待定 |
| R-2.5 | 本体单一事实源：专业可引用平台通用本体、禁改写 | 未实现 | design §9.1 N5 |

## R-3 内容工程（design §3.1–3.3）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-3.1 | 多格式文档加载（md/pdf/txt/xlsx/csv/docx） | 已实现 | 每 loader 输出统一 {text, chunks, source_meta} |
| R-3.2 | 上传两步契约：dry-run conflicts → 确认覆盖 | 已实现 | 同名不同 hash 不静默覆盖 |
| R-3.3 | 摄取链路：分段→摘要+多标签→聚合→embedding→入库 | 已实现 | 原子单位=chunk；对 summary 做 embedding |
| R-3.4 | 检索：向量命中 summary → 回取原文（0 LLM） | 已实现 | 证据带 doc_id/seq/原文 |
| R-3.5 | embedding 模型全程锁定（bge-m3） | 已实现 | kv 记录，替换需显式处理 |
| R-3.6 | 领域标签闭集（6 类），闭集外丢弃 | 已实现 | 确定性校验 |
| R-3.7 | 本体候选提名：LLM 抽取带 mentions → 三关校验（结构/语义/证据） | 已实现 | 拒绝带原因 |
| R-3.8 | 候选状态机：proposed→approved/rejected/merged/suspended | 已实现 | 用户终审 |
| R-3.9 | 二次编排（删/并/留，规则+GLM 分诊） | 已实现 | 批量预览 + 一键落地/丢弃 |
| R-3.10 | 数字人本体装配三段筛（锚点召回→规则排除→GLM 分诊） | 已实现 | 确认式拷贝进 persona_ontology |
| R-3.11 | 身份预筛：高频词→LLM 提名身份+锚点→用户审批 | 已实现 | 锚点=核心关注 |
| R-3.12 | 岗位归属与 type 优先级（组织架构） | 已实现 | 图谱按角色子树聚焦 |
| R-3.13 | 三角色互斥：scan/repair/ontology 同时只能一个 | 已实现 | mutex 闸门 |

## R-4 对话（design §3.4）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-4.1 | 多会话历史（chat_sessions / messages，session 可跨数字人） | 已实现 | 气泡上方标来源数字人 |
| R-4.2 | 确定性自动路由（0 LLM，本体+锚点打分） | 已实现 | 无匹配提示换说法 |
| R-4.3 | 动态检索窗口（锚点固定 → L1 命中 → L2 关系 → 预算扩展 L3/L4） | 已实现 | 上限 400/深度 2/预算 20% |
| R-4.4 | 概念抽取兜底（#106）：LLM 只出概念词，代码挑种子 | 已实现 | 对话路径启用；benchmark 不启用 |
| R-4.5 | SSE 流式对话（token 级） | 已实现 | session→token→done/error；前端逐 token 渲染 |
| R-4.6 | 对话中动作调用循环（≤3 轮，guard 裁决） | 已实现（非流式端点） | tool 型走 /api/chat 一次性；流式端点不循环（design §3.4 边界） |
| R-4.7 | RAG 参考资料可选注入（与本体叠加；失败优雅降级） | 已实现 | use_rag 开关 |
| R-4.8 | 对话气泡 markdown 渲染 | 已实现 | ReactMarkdown + 深色样式 |
| R-4.9 | 立即反馈 + 错误写气泡（不死等） | 已实现 | 占位气泡「判断路由中→由 X 组织语言→token 累积」 |

## R-5 测试与训练（design §4）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-5.1 | 四组对照 benchmark（G0 裸/G1 本体/G2 RAG/G3 叠加） | 已实现 | 指标见 test-metrics §T-B |
| R-5.2 | 判卷闭环：GLM 提名 verdict → 确定性终审（闭集+拒答覆盖） | 已实现 | 结论模板 0 LLM |
| R-5.3 | 错误归因 → 问题本体提名 → 用户逐条审 | 已实现 | name 闭集校验 |
| R-5.4 | 本体版本管理：快照→merge→v+1；可回滚（回滚前再快照） | 已实现 | persona_ontology_versions |
| R-5.5 | 能力题（HumanEval 164 + 项目相关题） | 已实现 | 任务+隐藏测试 |
| R-5.6 | 可执行验证（跑容器 assert 定 pass/fail） | 已实现 | 沙箱边界：无网络/只读/cap_drop/资源限额 |
| R-5.7 | 反应式写→测→改循环（≤3 轮） | 已实现（开关） | reactive=True 才走 |
| R-5.8 | 训练师闭环：选训练集→基线→失败归因→补本体→复测 | 已实现 | improvement = 复测−基线 pass_rate |
| R-5.9 | 基线多数票降噪（samples） | 已实现 | pass 数过半判 pass |
| R-5.10 | 双 LLM 考核（闭卷→异源开卷→三关终审） | 已实现 | 无判别器直接失败 |
| R-5.11 | 能力通过 → 沉淀为工具（git commit，可回滚） | 已实现 | 每能力一 commit |

## R-6 MCP 与工具（design §5.1–5.2）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-6.1 | MCP 注册与 docker 化启停 | 已实现 | mcp_servers 生命周期管理 |
| R-6.2 | MCP 审批门（pending→approved 才可启动） | 已实现 | 默认 admin |
| R-6.3 | 自动导入：mcp_imports/*.json → 幂等 upsert | 已实现 | 启动时扫描 + 手动触发 |
| R-6.4 | execute_mcp：stdio JSON-RPC 打通 | 已实现 | 8 字节 multiplexed header 解包 |
| R-6.5 | 镜像预拉基础镜像（CN 前缀），避免隐式 auto-pull 到失效 daocloud | 已实现 | _ensure_image |
| R-6.6 | 对话生成 MCP（六阶段协作 → 定义+server+Dockerfile → 导入待审批） | 已实现 | 见 design §5.2 |
| R-6.7 | MCP 工具绑定到数字人（actions） | 已实现 | IdentityPanel 可调用 MCP 面板 |
| R-6.8 | 前端展示 tool input_schema + called_by | 已实现 | McpPage |

## R-7 pipeline 编排（design §5.3）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-7.1 | pipeline 为一等公民：nodes/relations/changes/runs/handoffs | 已实现 | 表已落地 |
| R-7.2 | 关系类型：handoff / review（审核门）/ ask（询问回退） | 已实现 | review=红粗线+菱形门；ask=蓝虚线 |
| R-7.3 | ask 反向边排除出环检测/topo/布局 | 已实现 | 不参与 DAG 计算 |
| R-7.4 | pipeline 校验/批准/运行（job） | 已实现 | 非法拒绝带原因 |
| R-7.5 | 对话改流程（LLM 提名 change → 批准/拒绝） | 已实现 | pipeline_changes |
| R-7.6 | 对话生成 pipeline（LLM 设计节点+关系 → draft） | 已实现 | 见 design §5.2 |
| R-7.7 | 深色 SVG 编排图 + 审核门 + ask | 已实现 | PipelinePage |
| R-7.8 | 训练师接入 pipeline（train on pipeline） | 已实现 | TrainerPanel |
| R-7.9 | 上下文管理器（每数字人通用）：交接物带 kind + 预算 + 超限自缩减 | 已实现 | context_mgr（design §5.3.1） |
| R-7.10 | 节点数据契约：全祖先收集 + 角色入站白名单 + 按 kind 注入 | 已实现 | design §5.3.2（collect_inputs/refine_node_output） |
| R-7.11 | reviewer 关键上下文：任务 docstring + 隐藏测试断言 + 全量失败输出 | 已实现 | capability._review_diagnose |
| R-7.12 | 流程图接口体现：节点入站/出站 kind + 边传递内容标注 | 已实现 | PipelinePage 接口行 + label |

## R-8 调研与知识入库（design §5.4）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-8.1 | 多源搜索（OpenAlex/DBLP/Semantic Scholar/Google Scholar） | 已实现 | OpenAlex + DBLP 无需代理 |
| R-8.2 | 置信度确定性分级（期刊100>会议80>预印本60>学位40） | 已实现 | 非 LLM 排序 |
| R-8.3 | 跨源聚合 → 入库 RAG | 已实现 | ingest_results |
| R-8.4 | 定时/周期持续调研 | **未实现** | 依赖 U1 调度器（design §9.2 U1） |

## R-9 平台工程（design §6）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-9.1 | PostgreSQL 单一存储（数据+任务态+配置） | 已实现 | 自 SQLite 迁移完成 |
| R-9.2 | pgvector embedding 检索 | 已实现 | cosine ≤&gt; + jsonb source_meta |
| R-9.3 | 一切长操作皆 job + 事件流 + checkpoint | 已实现 | 断点续跑 |
| R-9.4 | 并发提取自适应（4 起，失败-1/连 8 成功+1；429 重排队） | 已实现 | |
| R-9.5 | 登录认证 + 角色 | 已实现 | HMAC token + bcrypt |
| R-9.6 | 设置持久化（llm/llm2/embedding/llm_mode） | 已实现 | settings.json + 环境变量覆盖 |
| R-9.7 | 双通道 LLM（V4-Flash 提名 / GLM 判别对话）+ 本地 Ollama 基线 | 已实现 | llm/llm2/ollama |
| R-9.8 | 远程 LLM 可选代理（LLM_PROXY） | 已实现 | 容器默认直连（当前环境无需代理） |
| R-9.9 | 全量备份导出/导入（可 git 提交 + LFS） | 已实现 | backup.py |
| R-9.10 | 多会话清理/重命名/删除 | 已实现 | chat_sessions CRUD |

## R-10 前端（design §7）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-10.1 | 登录页 | 已实现 | |
| R-10.2 | 对话页：会话组 + 流式 + markdown + 生成 MCP/pipeline 气泡 | 已实现 | 深色主题 |
| R-10.3 | 测试页（选 persona + A/B 对比） | 已实现 | ChatPage |
| R-10.4 | 数字人管理中心（已有/备选/创造 + 装配 + MCP 绑定 + reactive） | 已实现 | IdentityPanel |
| R-10.5 | RAG 预览（文档/chunk/检索/详情） | 已实现 | RagPage |
| R-10.6 | 本体图谱（ReactFlow + 角色子树 + 二次编排门） | 已实现 | OntologyPage |
| R-10.7 | Pipeline 编排 + 训练师 | 已实现 | PipelinePage + TrainerPanel |
| R-10.8 | MCP 面板（schema + called_by） | 已实现 | McpPage |
| R-10.9 | 设置页 | 已实现 | SettingsPage |
| R-10.10 | 深色主题统一 | 已实现 | styles.css :root |
| R-10.11 | 事件流自动重连（WS 指数退避 + seq 补齐） | 已实现 | useEvents |

## R-11 部署与运维（design §8）

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-11.1 | 全 docker 一键启动（start1.ps1 / start.sh） | 已实现 | dev/prod 区分 |
| R-11.2 | dev 复用已有镜像秒起；显式 -Rebuild | 已实现 | 镜像不存在自动 build |
| R-11.3 | Docker Desktop docker CLI 探测（固定路径 + PATH） | 已实现 | 修 Get-Command 误判 |
| R-11.4 | 脚本纯 ASCII（PowerShell 5.1 GBK 坑） | 已实现 | 无中文注释 |
| R-11.5 | 旧 venv 架构（start.ps1 + WSL pg） | 已弃用 | 迁移到全 docker |
| R-11.6 | 前端 no-cache（HTML 强制 revalidate） | 已实现 | _NoCacheStatic 继承 StaticFiles |

## R-12 已设计未实现项（design §9）

| ID | 需求 | 对应 design | 说明 |
|---|---|---|---|
| R-12.1 | 平台通用本体库 + 手工定义 + 版本回滚 | §9.1 N1 | |
| R-12.2 | Spec 编译器（五类产物） | §9.1 N3 | |
| R-12.3 | Reflection 复盘-回流-重编译闭环 | §9.3 | 六元已定义未闭环 |
| R-12.4 | 定时调度器 | §9.2 U1 | 持续调研⑥依赖 |
| R-12.5 | 分时用模（白天本地/夜间公司） | §9.2 U2 | |
| R-12.6 | OCR（扫描件） | §9.2 U3 | 明确不做 |
| R-12.7 | 基础数字人允许带 RAG（A-2）决策 | §9.1 歧义 | 待定 |
| R-12.8 | 装配取源分支（N2）/六元语 actions-guardians-interface 表（N4）/禁改写（N5）/技能包挂载位（N6）/工具工厂编译器（N7） | §9.1 | |
| R-12.9 | 编排-动态建数字人全自动入口（调研并写 demo 页面） | §9.4 | 概念验证已设计 |
| R-12.10 | 前端上传「＋」按钮开放 | §9.4 | 暂占位 |

## R-13 统一消息协议（design §10）

> 目标：**所有聊天（对话 / 能力题 / 本体抽取 / 装配分诊 / pipeline 设计 / 训练师归因 / 考核）一律用 JSON 在 LLM 中传递**，入参与出参双向统一。

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-13.1 | 统一消息信封（Envelope：v/kind/from/to/payload/task/refs/meta） | **已实现** | kind 10 类闭集（design §10.2）；`protocol.Envelope` |
| R-13.2 | 入参打包：`instruction` + `context` 结构化；本体/RAG 条目带 `ref` | **部分实现** | `pack_instruction/context/question/answer/tool_result` 已提供；**尚未接入 `chat._system_prompt`** |
| R-13.3 | 出参结构化：`llm.structured()` 统一入口 + JSON 模式 | 未实现 | 需接入 DeepSeek/GLM/Ollama 的 `json_object` / `format=json` |
| R-13.4 | JSON 解析器收敛（**5 套 → 1 套** `protocol.parse_json`） | **已实现** | llm / pipeline / main×2 / mcp 五处私有实现全部退役（比设计预估多一套） |
| R-13.5 | provider 分发去重（3 处 → 1 处 `llm.dispatch`） | 未实现 | chat/capability/llm 三处重复仍在 |
| R-13.6 | 流式增量 JSON 解码（`StreamJsonReader`），流式体验不退 | **已实现** | 增量拼接 == 最终 `payload.text`；`finish()` 失败可降级 raw |
| R-13.7 | legacy 兼容旧文本会话 + `DMP_MODE=off` 一键回退 | **已实现** | 纯文本自动包 legacy 信封；`protocol.dmp_enabled()` |
| R-13.8 | 校验失败分链：降级链回落规则，终审链显式失败 | 未实现 | 依赖 R-13.3 落地 |

## R-14 内容类型体系（design §11）

> 目标：chunk 带 `type`，type 与「置信度」「是否绝对遵守」两维度相关，且词表**用户可自定义**。

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-14.1 | `chunk_types` 词表表（用户可增删改；8 条 builtin 预设含 `unknown` 兜底） | **已实现** | builtin / unknown 不可删；被引用不可删 |
| R-14.2 | chunk 新增 `type` / `type_confidence` / `type_mandatory` / `type_source` | **已实现** | idempotent ALTER 迁移 + 索引（建表 13 列、迁移后补列） |
| R-14.3 | 判定链：LLM **只提名 type**，两维度由词表**确定性裁决** | **已实现** | `chunk_types.resolve`；提名不直接落库（铁律 L1） |
| R-14.4 | 未知类型不臆造（不在 active 词表 → `unknown`） | **已实现** | 对齐铁律 L2；`None`/拼错同样回落 |
| R-14.5 | `mandatory=2` 必选注入上下文，不受 top-k 截断 | **已实现** | `_rag_snippets` 分两路，独立【强制约束】段 |
| R-14.6 | 检索/列表 API 增加三维过滤与输出 | **已实现** | search + list_chunks 支持 type/confidence/mandatory |
| R-14.7 | 前端：chunk type 列 + 筛选 chips + 「类型词表」管理面板 + 单条终审改 type | **已实现** | 徽章配色按 mandatory（红/橙/灰） |

## R-15 RAG 摄取数字人（design §12）

> 目标：新增「知识摄取官」数字人，把摄取契约与 type 体系作为其本体。

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-15.1 | 新增数字人「知识摄取官」（code `knowledge_ingestor`，category=general） | **已实现** | `seed_ingest_identity.py` 幂等 upsert（id=8, approved） |
| R-15.2 | 15 条摄取契约本体（8 概念 + 7 规则）经装配审批入池 | **已实现** | 含分块/去重/覆盖/类型判定/证据/强制注入规则；另加 4 条锚点 |
| R-15.3 | 4 条 actions：rag_ingest / chunk_classify / chunk_search / ontology_extract | **已实现** | `persona_actions` builtin（status=approved） |
| R-15.4 | **权责边界**：数字人不参与逐条判定，摄取执行仍是确定性 job | **已实现** | `ingest.py` 不 import 该数字人；零耦合 |
| R-15.5 | 类型词表维护 + 复盘提名（一期只读统计：unknown 占比 / type 分布） | **部分实现** | 词表维护已可用（R-14.7）；复盘统计卡片待 §9.3 闭环，工作台「复盘」Tab 已占位 |

## R-16 数字人工作台（design §13）

> 目标：把「数字人创建台」与数字人管理中心**整合为一个页面**，同时解决"不好看 + 逻辑不顺"。

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-16.1 | 合并 ingest tab 与 IdentityPanel 为单页「数字人工作台」 | **已实现** | 导航 `ingest` → `identity`（label「数字人」） |
| R-16.2 | 列表—详情（master-detail）+ 详情五 Tab（概览/知识与本体/能力与工具/测试与版本/复盘） | **已实现** | 替代原"平铺三块 + 5 个折叠块" |
| R-16.3 | 创建向导三来源合一（自主提名 / 图谱种子 / 空白模板） | **已实现** | 两步向导；消除"两个入口同一后端行为" |
| R-16.4 | 装配流程内联为一条时间线（5 步 → ≤2 步，事件驱动刷新） | **部分实现** | 装配块已就地内联在「知识与本体」Tab；**事件驱动刷新未做**（仍靠 refreshKey） |
| R-16.5 | 状态收敛：23 个 useState → `useWorkbench` + 按需加载 | **已实现** | `hooks/useWorkbench.ts`；**组件内 useState 归零**；只拉选中项详情（J-3/J-4 达标） |
| R-16.6 | 任务与事件改为全局抽屉（任何页面可开） | **已实现** | 顶栏「任务」按钮 + 右侧抽屉（含运行中计数） |
| R-16.7 | OntologyPage 移除内嵌管理中心，回归纯图谱 | **已实现** | 页面职责单一 |
| R-16.8 | 待审队列分离 pending / rejected；「拒绝」明确为**拒绝并删除**（二次确认） | **部分实现** | 列表筛选「待审 / 已拒」已分离，「删除已拒」文案已区分；**拒绝的二次确认弹窗未加** |

## R-17 DFMEA 自动编排链路（design §15）

> 目标：用户一句需求 → 自动生成由 **N 个部件专家数字人 + 1 个 DFMEA 工程师**（+ 复核门）组成的流水线，产出带**逐格来源标注**的 DFMEA 报告。

| ID | 需求 | 状态 | 验收要点 |
|---|---|---|---|
| R-17.1 | 需求 → 自动生成 DFMEA pipeline（复用 `/api/pipeline/generate`） | **已实现** | 产物落到 draft，节点已绑数字人（实测 0 未绑定） |
| R-17.2 | 取值优先级链：历史库 → AP/S-O-D 表 → 专家 → AI 推断 | **已实现** | 由 DFMEA 工程师本体承载；**不得越级代填**为规则条目 |
| R-17.3 | 逐格来源标注（`history`/`table`/`expert:<name>`/`ai_inferred`/`ai_new`） | **已实现** | 代码侧闭集裁决（`fmea.validate_sources`），越界拒绝写入 |
| R-17.4 | 全新功能无依据 → 强制标注「AI 生成·待人工确认」 | **已实现** | `ai_new` 可被 `pending_ai_new()` 机器筛出 |
| R-17.5 | DFMEA 工程师数字人（本体 = 取值链与标注规则） | **已实现** | id=9，本体 22 条（8 概念 + 14 规则） |
| R-17.6 | 部件专家数字人 × N | **已实现** | id=10~13（射频/电源时钟/结构工艺/固件），各 5 条本体 |
| R-17.7 | 历史 FMEA 数据域（结构化表 + 导入） | **已实现** | `fmea_cases` 17 条蓝牙模块样例 + 30 条 S/O/D 准则 + 1000 格 AP 表 |
| R-17.8 | P0 动作：`fmea_history_query` / `ask_expert` / `fmea_write_row` | **已实现** | 另含 `fmea_ap_table`；四个动作均支持**批量**（取准则/查 AP/写多行） |
| R-17.9 | pipeline **ask 回退可执行**（引擎级） | **已实现** | `_ask_sources` + `fmea.allowed_experts`；问名单外专家被拒，深度上限 2 |
| R-17.10 | 生成时**自动绑定 persona_id** | **已实现** | `generate_from_request` 校验并绑定；实测 0 未绑定 |
| R-17.11 | review 门控 + FMEA 专用 kind + handoff 契约校验 | **部分实现** | 门控与 kind 已实现（FAIL 中止下游）；**handoff_schema 运行期校验未做** |
| R-17.12 | DFMEA 表格视图（来源高亮 + 待人工确认清单） | **已实现** | 对话页 `PipelineCard`：逐格来源徽章 + `ai_new` 待确认清单 |
| R-17.13 | DFMEA 结果导出 xlsx/csv | 未实现 | P2 |

---

## R-18 数字人模板（六元组蓝图 + 填空槽位）（design §16）

| 编号 | 需求 | 状态 | 约束/说明 |
|---|---|---|---|
| R-18.1 | `persona_templates` 表：模板 = 槽位 + 蓝图（用户可自定义，内置不可删） | **已实现** | 与 `chunk_types` 同一策略 |
| R-18.2 | 槽位渲染：`{{slot}}` 递归替换 + 必填校验，**不经过 LLM** | **已实现** | 结果可复现 |
| R-18.3 | `type=ontology` 槽位：多行「类型\|名称\|定义」解析为本体条目并追加 | **已实现** | 使通用模板可派生任意领域专家 |
| R-18.4 | 动作只存 `builtin_name`，schema 从 `BUILTIN_ACTIONS` 取 | **已实现** | 消除 seed 里的重复定义 |
| R-18.5 | `instantiate`：身份 + 锚点 + 本体 + 动作 + owns 关系，按 name 幂等 | **已实现** | 重跑不产生重复 |
| R-18.6 | 7 个 API：列表 / 详情 / 预览（**不落库**）/ 实例化 / 增改删 | **已实现** | — |
| R-18.7 | 前端：「新建数字人」第 1 步加「套用模板」+ 模板弹窗（动态槽位 + 实时预览） | **已实现** | 预览防抖 300ms |
| R-18.8 | `seed_dfmea_identities.py` 改为**模板驱动**（单一事实源） | **已实现** | 与 UI 走同一条代码路径 |
| R-18.9 | **反向沉淀**：现有数字人 → 模板（`from-identity`） | **已实现** | 读身份+锚点+本体+动作+owns 拼回 blueprint |
| R-18.10 | **往返一致性**：槽位 default = 原词，空填写渲染须还原原数字人 | **已实现** | 逐字段相等；24 项断言 |
| R-18.11 | `reset_builtin`：「恢复内置蓝图」显式入口 | **已实现** | 恢复出厂语义，**不进启动路径** |
| R-18.12 | 前端模板管理面板（编辑/停用/删除/新建 + 蓝图 JSON 高级编辑） | **已实现** | 内置项删除按钮置灰 |

---

## R-19 WiFi 场景：部件知识库 + 自主搜索 + 考官制验收（design §15.7）

| 编号 | 需求 | 状态 | 约束/说明 |
|---|---|---|---|
| R-19.1 | `fmea_parts` 部件知识库（产品/子系统/功能/工况/关键词/类比线索） | **已实现** | **不含失效模式** —— 必须由链路自行推导 |
| R-19.2 | `seed_fmea_parts.py`：WiFi 模块 13 个子系统 | **已实现** | `note` 写"可类比的历史部件族" |
| R-19.3 | 动作 `fmea_part_search`：自主搜索部件清单 + 历史部件族统计 | **已实现** | 返回 `parts` / `history_parts` |
| R-19.4 | DFMEA 工程师本体加「部件清单先行」+「同类案例类比」规则 | **已实现** | — |
| R-19.5 | 生成 prompt 附**每个数字人已批准的动作**，并要求按动作分工 | **已实现** | 否则「搜索部件」会被派给没有该动作的角色 |
| R-19.6 | 考官制考卷 `grade_dfmea_wifi.py`：30 题 × 5 判定点 = 150 点 | **已实现** | 正确率阈值 **98%** |
| R-19.7 | 端到端 `verify_dfmea_wifi_e2e.py`（G 生成 / R 运行 / V 判分） | **已实现** | 需求由对话页一句话给出 |
| R-19.8 | `part` 列**确定性归一**为中文子系统名（族代码 → 中文名） | **已实现**（design §15.8 ①） | `fmea.canonical_part()`；多子系统族按失效模式关键词落位 |
| R-19.9 | 询问专家**确定性登记**，并核验「问过谁 vs 表里引用谁」差集 | **已实现**（design §15.8 ②） | `register_consulted_expert()` + 动作「核验专家引用可追溯」 |
| R-19.10 | 交付物闸门：轮数用尽后**强制一轮「只准写结论」** | **已实现**（design §15.8 ③） | 防「裸 tool_call 草稿当交付物」 |
| R-19.11 | 重试后仍非交付物 → 节点判 failed 并**中止整条 run** | **已实现**（design §15.8 ③） | 不再把草稿下传，错误信息直指真实原因 |
| R-19.12 | **交付物 → 落库必须在复核门之前**（引擎自动持久化） | **已实现**（design §15.8 ①） | `fmea.parse_table_rows()` + `fmea.ingest_table_rows()`；复核门 FAIL 不再清零结果表 |
| R-19.13 | 表格解析支持**两种形态**（行内 `字段=值` / markdown 表）且**绝不臆造来源** | **已实现**（design §15.8 ①） | 无 `sources` 的行明确跳过并报原因；`sources` 是合同必填 |
| R-19.14 | 汇总节点出站预算 4000 → **12000**，且落库读**原始产出** | **已实现**（design §15.8 ②） | 表不能靠"精炼"保真；精炼后文本会丢掉后半表 |
| R-19.15 | 部件知识库为空时 `part_search` **必须响亮报错**（`ok=False` + 修复指令），不得静默返回空表 | **已实现**（design §15.8-12 ①） | 空表与「产品名没匹配上」在返回值上不可区分 → 模型反复重试直到轮数耗尽 |
| R-19.16 | 产品名**模糊匹配**：库内名与任务名不必字面相等（四段递进 + token 重叠打分） | **已实现**（design §15.8-12 ③） | `WiFi 模块` ↔ `射频无线模块（蓝牙/WiFi）` 两种写法均返回 13 条子系统 |
| R-19.17 | 每部件带 `analogy_family`，可直接喂 `history_query(family=…)` | **已实现**（design §15.8-12 ③） | 类比推导从「靠模型从提示里抠缩写」变成「拿去就用」 |
| R-19.18 | 考卷驱动**前置夹具自检**（四张表点数），缺件则拒绝开跑 | **已实现**（design §15.8-12 ②） | 判据：**夹具缺失 ≠ 能力不足**，宁可不出分也不出假低分 |
| R-19.19 | `part_search` 必须**区分**「表真空」/「query 没命中」/「产品名不存在」三类，不得把 query 未命中误报为知识库为空 | **已实现**（design §15.8-13 ①） | 判据是「该 product 在表里总共几行」而非「过滤后是否有行」；误报会让模型**主动放弃全部子系统** |
| R-19.20 | query 未命中时回 `query_miss` + `available_subsystems`（可用子系统名清单） | **已实现**（design §15.8-13 ①） | 让「改词重查」有依据，而不是被误导成「知识库坏了」 |
| R-19.21 | **部件专家必须能查历史 FMEA**：`part_expert` 模板绑定 `fmea_history_query` | **已实现**（design §15.8-13 ②） | 修复前专家只有「检索本体 + 检索 RAG」两个动作，够不着装着 17 条真实案例的 `fmea_cases` → 4 个专家全体交白卷 |
| R-19.22 | 部件专家本体须含「**证据优先级**」规则：① 历史 FMEA ②本体 ③RAG；历史有同族案例时**不得**以「我不知道」作答 | **已实现**（design §15.8-13 ②） | 把「不知道就说不知道」与「有证据必须用」两条铁律说清楚，避免诚实变成无能 |
| R-19.23 | 部件专家本体须含「**类比推导要求**」：逐条给出「历史案例 → 本子系统」迁移并标 `history#<id>` | **已实现**（design §15.8-13 ②） | 禁止只罗列历史案例而不做迁移 |
| R-19.24 | 数字人卡片提供**上传文档**按钮：上传 → 录入 RAG → **范围化**提取（仅新 chunk）→ 装配为**待确认** | **已实现**（design §13.7.1） | 用户是唯一终审点：卡片上**不出现**「自动采纳进本体段」按钮；限定模式不 resume（子集序号与全库检查点不同源） |
| R-19.25 | `part_search` 每部件须带 `analogy_cases`：把 note 里的 `BT-XXX-NN` 解析成**具体** `history#<id>` 列表 | **已实现**（design §15.7 迭代续 ④） | 族代码只说"去哪一族"，`analogy_cases` 才说"这个子系统对应哪几条"；**一个 part_no 可对应多条案例，必须收全**（`BT-ANT-01` → `history#1`+`#2`） |
| R-19.26 | 单个 nominate 节点的工具调用轮数上限须**足够跑完该节点的既定动作序列** | **已实现**（design §15.7 迭代续 ⑤） | `MAX_ACTION_ROUNDS` 8 → **14**；属工程边界，**来源闭集/AP 以表为准/逐格标注一律不放松** |
| R-19.27 | 复核门 FAIL 须触发**受限的修复回边**：把复核意见**原样**回放给上游写入节点 → 重跑 → 重审，**上限 2 轮** | **已实现**（design §15.7 迭代续 ⑥ / §15.3 E2） | 引擎只做「回放 + 重跑」，**不改写复核意见**（不介入生成）；仍不过才 `blocked` + 待人工确认清单 |
| R-19.28 | 每行 `action`（建议措施）**必须非空**；整列为空即判 FAIL 并中止下游 | **已实现**（design §15.7 迭代续 ⑦） | 双侧钉住：工程师「写行自检」④ + 复核员「复核准则」⑧ |
| R-19.29 | 一格 `sources` **只允许一个来源 token**（禁 `；;，,、\|/` 分隔符拼接） | **已实现**（design §15.7 迭代续 2 ⑧） | 原实现 `split("#",1)[0]` 只校验第一个 `#` 之前 → `"history#1；table#severity6"` 蒙混过关（题 16 共 38 处异常） |
| R-19.30 | 同格出现表引用 + 裸来源时**按位归**：表引用占该格、裸来源归 `failure_mode` | **已实现**（design §15.7 迭代续 2 ⑧） | `_merge_src` 一字段一 token，**绝不拼串**；裸来源的语义是"本条行类比自 history#N" |
| R-19.31 | 同一 run 内**最后交付物胜**：落库前清掉本段覆盖子系统的旧代 | **已实现**（design §15.7 迭代续 2 ⑨） | 修复轮的交付物是初轮草稿的修订版，应**取代**而非并列（原 `(part, failure_mode)` 幂等键挡不住 `failure_mode` 被改写的情形 → 双代残留 → `action` 整列出现 NULL） |
| R-19.32 | 未覆盖的子系统的已落库行**不得被误删** | **已实现**（design §15.7 迭代续 2 ⑨） | 只清本段交付物涉及的子系统；单测断言写「时钟源」不影响「天线阻抗失配」 |
| R-19.33 | `action` 解析在**两条路径**（形态 1 别名 / 形态 2 表头）都不缺件 | **已实现**（design §15.7 迭代续 2 ⑩） | 形态 1 别名表原**根本没有 `action`**；形态 2 新增 `_CN_HEADERS`（中文表头，长别名在前） |
| R-19.34 | 单字母列名 `S`/`O`/`D`/`AP` 必须能映射到字段 | **已实现**（design §15.7 迭代续 2 ⑪） | 原 `"severity" in "s"` 恒 False → S/O/D 三列全丢 → 中文表头表解析 **0 行** |
| R-19.35 | 单测断言须是**语义**而非**写死数量** | **已实现**（design §15.7 迭代续 2 ⑫） | `verify_persona_templates.py` 由 `n_act == 2` 改为「蓝图声明的动作全部绑定且 approved」——补零件不再误报回归 |
| R-19.36 | 单测的正确率**必须达到 98%**（30 题 × 5 判定点 = 150） | **已达 98.0%**（design §15.7 迭代续 3） | run#33：147/150，28/30 题全对；`status=done` 首次非 blocked |
| R-19.37 | 形态 1 须支持**裸值**写法（`S5 O4 D4 AP=M`，不写 `=`） | **已实现**（design §15.7 迭代续 3 ⑬） | `_cell` 对短字段（≤4 字符）加第二趟裸值匹配；`建议：` 亦识别为 `action` |
| R-19.38 | 「询问专家规则」不得与「取值优先级链」矛盾 | **已实现**（design §15.7 迭代续 3 ⑭） | 原文「仅在历史库与准则表都无结果时才询问专家」与「第 3 级 = 问专家」自相矛盾 → 改为「两个场景都必须问」 |
| R-19.39 | 有历史类比时也须**逐个专家问一次**做迁移确认，并落到 `expert:` 来源格 | **已实现**（design §15.7 迭代续 3 ⑭） | 历史库是**别的产品**的案例，跨产品迁移成立性必须有专家确认；复核员补第 ⑨ 条判据（`expert:` 0 处即 FAIL） |
| R-19.40 | 生成的拓扑须保证**专家数字人有 `ask` 回边**（不能只接 `supply`） | **已改为引擎侧兜底实现**（design §15.7 迭代续 4 ⑯） | run#33/#34 连续两场均无 `ask` 边 → 工程师无专家可问（题 17 根因）。**判定修正**：指望生成规则不可靠，改由引擎 `_materialize_ask_edges` **确定性补齐**——凡有专家节点 ⇒ 补「非专家 → 每个专家」的 `ask` 权限边；已有边不重复补（方向无关）；补齐仅在本 run 内存生效、不写回库 |
| R-19.41 | `_cell` 取值须支持**字段后仍有其它小节**的交付物（末字段不得漏抠） | **已实现**（design §15.7 迭代续 4 ⑰） | run#34 R13 的 `建议措施=…` 后紧跟 `\n\n### 缺失与待人工确认项…`，原 `$` 未开 `re.M` → `action=NULL`（题 21 失分）；加 `re.M` 后抠出 |
| R-19.42 | `_paren_sources` 须识别**带散文前缀的 `table#…`**（`按 table#severity8 调整：…`） | **已实现**（design §15.7 迭代续 4 ⑰） | 原 `startswith("table")` 因前缀 `按 ` 为 False → token 被静默丢弃 → severity 格无来源（题 14 失分）；改为 token 内定位 `table#` |
| R-19.43 | S/O/D/AP **有值却无来源**时，须回该格自身括号再找一次（找不到仍跳过、**不臆造**） | **已实现**（design §15.7 迭代续 4 ⑰） | 关键格来源兜底；保证「值在表里、依据必可追溯」，同时不违反来源闭集 |
| R-20 | pipeline 创建元流程（pipeline-factory）：需求解析 → 资产盘点 → 补齐 → 组装 → 编译考卷 → 考试迭代 → 冻结 → 反向沉淀 | **主体已实现**（design §18.6，commit 4a438e2） | 从 §15.7 十六轮迭代提取；factory 已落库 #47（9 节点 / 11 关系）；DFMEA 回迁与本体种子待做 |
| R-20.1 | 创建 pipeline 前须判定**是否需要专家数字人**（判据：交付物含领域判断且需独立复核；纯检索/变换步骤不需要） | **待实现**（design §18.1） | m1 需求解析为 nominate 节点（#7），判据已写入设计；本体种子未装 |
| R-20.2 | **专家就位三级降级**：模板实例化 → 相似模板派生 → 建数字人子流程；顺序不可反 | **实现中**（design §18.6①） | m3 补专家已接 deterministic 分支（返回模板清单 + 指引）；`instantiate` 与 `_run_build_persona` 均已存在 |
| R-20.3 | **资产盘点**：静态查出结构性缺口（数字人/模板/动作/MCP/夹具） | **已实现**（design §18.6①） | `pipeline_factory.inventory(conn)`：五类资产清单 + 摘要；实测 14 identities / 4 templates / fixtures_ok=True |
| R-20.4 | **补齐双触发**：结构性缺口由盘点触发；语义性缺口由考试归因触发（同一机制、两个触发源） | **实现中**（design §18.6①） | `diagnose()` 已做 structural/semantic 启发式分类；LLM 提名归因段待接 |
| R-20.5 | **考卷编译**：三型题目从夹具与不变量自动生成（覆盖型←对象清单；规则型←不变量；基准型←基准库） | **已实现**（design §18.6①） | `compile_exam(conn)`：38 判定点（覆盖13/规则7/基准18）自动产出，纯 deterministic |
| R-20.6 | **考试循环**：G/R/V + 归因 + 有界复跑；考官不干预、判分只读产出 | **实现中**（design §18.6①） | `run_exam`（只读 dfmea_rows，不调 LLM）+ `diagnose` 已落地；有界复跑循环与 G 段生成编排待接 |
| R-20.7 | **反向沉淀**：达标后新本体回流模板库 | **实现中**（design §18.6①） | m9 入口已接（指引 §16.5 `identity_to_template`）；端到端未验证 |
| R-20.8 | **DFMEA 回迁**为元流程第一个验收实例（`grade_dfmea_wifi.py` 改写为 ExamSpec 实例） | **待实现**（design §18.5-5 / §18.6④） | 38 判定点与 30 题×150 点口径对齐尚需逐条核对；不回迁则抽象未被证明 |
| R-20.9 | 元流程自身须过**创建四关**（准入/结构/能力/验收）；当前会 fail 能力关（m2/m6/m7 待建）→ **先补零件再组装** | **已满足**（design §18.6） | m2/m6/m7 承载能力已落地（commit 4a438e2）；factory #47 的 deterministic 节点全部有可用函数 |
| R-20.10 | **pipeline 版本族**：通过考试的为 canonical，历次迭代归入 family 历史版本（不删、可查、默认隐藏） | **已实现**（design §18.6②） | `pipelines` 加 `is_archived/family_id/parent_version_id` 三列 + 索引；迁移脚本幂等；当前 #44 CANONICAL、26 条归档 family=44、#1 归档 family=1 |
| R-20.11 | **画布交互**：pipeline 流程图须支持节点拖动（位置持久化）、画布平移缩放、撤销/重做 | **已实现**（design §18.6③） | `PipelineGraph` 交互化：拖动落库 position_x/y、viewBox 平移、滚轮 0.4x~2.5x、Ctrl+Z 双栈；列表默认过滤归档 + 勾选可查 |
| R-21 | 本体库同步链路：数字人本体段（persona_ontology）须同步进本体库（candidates），前端「知识与本体」页可见 | **已实现**（design §19.1） | 缺陷实测：146 条 persona_ontology 里 84 条未同步（trainer.add_ontology 只写单侧）。修复：add_ontology 双写 + `_sync_candidate`（name_norm 幂等、只补不改、tags 带 persona:来源）；回填脚本补存量：candidates 68 → 149，缺口 0 |
| R-21.1 | **资产打包导出**：数字人 + 本体库 + 本体段/动作/锚点 + 本体关系 + pipeline + MCP 一键导出 JSON（**不含 RAG 数据**） | **已实现**（design §19.2） | `porter.export_bundle`：8 个 section；实测导出 14/146/37/14/149/77/29/5 |
| R-21.2 | **资产打包导入**：导入 bundle 须幂等（按 name 判重、只补不改），ID 按 name 重映射（跨库安全） | **已实现**（design §19.2） | 同 bundle 重导入 added=0 / skipped=471（幂等验证通过）；persona_id 按 name 重映射 |
| R-21.3 | 导出/导入须有**前端入口**（Settings 页）与导入报告 | **已实现**（design §19.2） | 导出下载 `digitalman-bundle-<ts>.json`；导入后展示逐 section added/skipped |
| R-22 | 数字人创建统一收口：消除平行建身份实现，产物完整度由 spec 显式声明 | **已实现**（design §20） | 耦合确诊：`identity.create_identity` 与 `persona_templates._upsert_identity` 两套平行实现（校验分叉：模板写 keywords、图谱硬编码 "[]"；模板无 MAX_NAME_LEN 校验）；6 条创建路径产物完整度不一致 |
| R-22.1 | `upsert_identity` 作为**全平台唯一** identities 行写入口（校验唯一份；update_if_exists 双语义） | **已实现**（design §20.2①） | 单测 5/5：幂等同 id / 更新生效 / keywords 保留 / 空名拒绝 / 超长拒绝 |
| R-22.2 | 模板路径收口：`_upsert_identity` 改薄壳调统一入口 | **已实现**（design §20.2②） | `verify_persona_templates.py` RESULT: OK（收口后无回归） |
| R-22.3 | 规范 API `POST /api/identities`（IdentitySpec：reactive/内联本体/动作绑定）；旧路由留 alias；前端切换 | **已实现**（design §20.2③） | 冒烟：创建带内联本体 → ok + inline_ontology=1，且同步进本体库 1 条；`api.ts` 已切新路由 |
| R-22.4 | 演进方向：创建入口进一步收口到 §18 factory（m1~m4），IdentitySpec 成为 factory 产出物 | **远期**（design §20.4） | 待 §18 factory 端到端跑通（LLM 账户充值后） |

---

*追溯说明：每条需求的状态、验收与 design § 同步维护；新增需求按 design §14 流程登记编号（R-17+）。*
