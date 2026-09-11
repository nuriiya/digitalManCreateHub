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

*追溯说明：每条需求的状态、验收与 design § 同步维护；新增需求按 design §14 流程登记编号（R-17+）。*
