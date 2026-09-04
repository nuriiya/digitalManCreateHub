# pipeline 架构调研（2026-09）

> 调研日期：2026-09-04 · 关联待办 `r55MT6`（市面数字人本体调研）与 `rsE0kc`（搭建 pipeline）
> 范围：多智能体编排框架、持久执行引擎、协议层、本体驱动编排参照
> 目的：回答「搭建 pipeline 时，哪些能用现成的、哪些必须自己造」

---

## 0. 结论先行

1. **不缺编排引擎，缺的是编排图的资产化**。LangGraph / CrewAI 之类的框架解决的是「怎么把一串 LLM 调用串起来」，而本项目真正缺的是「通路库 + 编排图的持久化、版本化与确定性校验」——**这三样任何框架都不提供，必须自研**。
2. **CrewAI 不适合**：它的 agent 绑定在 crew 生命周期内，不能跨会话独立常驻，与「数字人常驻复用」直接冲突。
3. **AutoGen 不建议**：微软已把战略重心转向 Microsoft Agent Framework，AutoGen 只做 bug 修复与安全补丁，不再有重要新功能。
4. **LangGraph 可选但收益有限**：它的核心卖点（checkpoint、human-in-the-loop、持久化）本项目 `jobs.py` 已具备等价能力；引入要付出 LangChain 生态耦合与依赖冲突的代价。
5. **Temporal 值得关注但优先级低**：等真正出现「跑几小时到几天、中间要人工审批挂起」的场景再引入。
6. **MCP 建议采纳**：工具自生长需要一个标准的工具描述与调用协议，MCP 就是现成的，且它是协议不是服务，私有化部署不受影响。
7. **A2A 暂缓，但要借它的 Agent Card**：跨组织互操作现在用不上，但「每个数字人有一张描述我能做什么、怎么调用的卡片」正好是本项目的 interface 段。
8. **最重要的参照是 Palantir AIP 的确定性运行时**——它的设计原则与本项目的三条铁律几乎逐条对应，且已被大规模验证。

> 一句提醒（来自 Presenc AI 的 2026 调研）：**框架选择在多智能体系统成功要素里排第四**，前三依次是模型选择、评测基础设施、人工检查点设计。本项目已有 benchmark（评测）与审批闸门（人工检查点），恰是最难的两块——投入方向是对的。

---

## 1. 分层架构图

<svg viewBox="0 0 680 476" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<title>pipeline 分层架构：现成组件与自研部分的边界</title>
<desc>pipeline 分四层：编排层决定谁先谁后、持久执行层决定跑一半怎么办、协议层决定怎么接工具与外域、本体层决定什么是对错。前三层都有成熟的现成组件可选用，第四层本体层决定对错，任何框架都不提供，必须自研。</desc>
<defs>
<marker id="arrowL" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
<path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</marker>
</defs>
<text x="340" y="24" text-anchor="middle" font-size="14" font-weight="500" fill="#F1EFE8">pipeline 四层：哪些能买、哪些必须自己造</text>

<rect x="40" y="44" width="600" height="100" rx="12" fill="#042C53" stroke="#85B7EB" stroke-width="0.5"/>
<text x="60" y="68" font-size="13" font-weight="500" fill="#B5D4F4">编排层 · 决定谁先谁后</text>
<text x="620" y="68" text-anchor="end" font-size="11" fill="#85B7EB">候选：LangGraph / 自研</text>
<rect x="60" y="80" width="180" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="150" y="98" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#E6F1FB">通路库 + 编排图</text>
<text x="150" y="118" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#B5D4F4">自研 · 无现成</text>
<rect x="250" y="80" width="180" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="340" y="98" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#E6F1FB">编排图校验器</text>
<text x="340" y="118" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#B5D4F4">自研 · 环/基数/悬空</text>
<rect x="440" y="80" width="180" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="530" y="98" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#E6F1FB">执行调度</text>
<text x="530" y="118" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#B5D4F4">现有 jobs 可担</text>
<path d="M340 144 V154" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL)"/>

<rect x="40" y="156" width="600" height="76" rx="12" fill="#042C53" stroke="#85B7EB" stroke-width="0.5"/>
<text x="60" y="180" font-size="13" font-weight="500" fill="#B5D4F4">持久执行层 · 决定跑一半怎么办</text>
<text x="620" y="180" text-anchor="end" font-size="11" fill="#85B7EB">候选：Temporal / 现有 jobs</text>
<rect x="60" y="190" width="280" height="34" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="200" y="207" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E6F1FB">checkpoint · 断点续跑 · 事件流（已有）</text>
<rect x="350" y="190" width="270" height="34" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="485" y="207" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E6F1FB">人工审批闸门（已有）</text>
<path d="M340 232 V242" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL)"/>

<rect x="40" y="244" width="600" height="76" rx="12" fill="#042C53" stroke="#85B7EB" stroke-width="0.5"/>
<text x="60" y="268" font-size="13" font-weight="500" fill="#B5D4F4">协议层 · 决定怎么接工具、怎么接外域</text>
<text x="620" y="268" text-anchor="end" font-size="11" fill="#85B7EB">MCP 已成熟 · A2A 暂缓</text>
<rect x="60" y="278" width="280" height="34" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="200" y="295" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E6F1FB">MCP：工具描述与调用（建议采纳）</text>
<rect x="350" y="278" width="270" height="34" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="485" y="295" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E6F1FB">A2A：跨组织（暂缓，借 Agent Card）</text>
<path d="M340 320 V330" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL)"/>

<rect x="40" y="332" width="600" height="100" rx="12" fill="#412402" stroke="#FAC775" stroke-width="0.5"/>
<text x="60" y="356" font-size="13" font-weight="500" fill="#FAC775">本体层 · 决定什么是对错（护城河）</text>
<text x="620" y="356" text-anchor="end" font-size="11" fill="#FAC775">必须自研 · 已建成大半</text>
<rect x="60" y="368" width="180" height="52" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="150" y="386" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">本体库 + 版本快照</text>
<text x="150" y="406" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">已有</text>
<rect x="250" y="368" width="180" height="52" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="340" y="386" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">三关校验</text>
<text x="340" y="406" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">结构 · 语义 · 证据</text>
<rect x="440" y="368" width="180" height="52" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="530" y="386" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">编译产物</text>
<text x="530" y="406" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">schema · SHACL · 状态机</text>

<text x="340" y="456" text-anchor="middle" font-size="12" fill="#888780">上三层只解决「执行」，第四层才决定「对错」——这一层任何框架都不提供</text>
</svg>

---

## 2. 多智能体编排框架对比

| 框架 | 核心抽象 | 生产成熟度 | 状态持久化 | 人工介入 | 可重放 | 与本项目契合 |
|---|---|---|---|---|---|---|
| **LangGraph** | 有向状态图（节点=步骤，边=状态流转） | 高（2025 末 v1.0，约 38% 生产部署占比） | 内建 checkpoint、时间旅行调试 | 原生 human-in-the-loop，可中断修改状态 | 支持 | 中：核心能力 `jobs.py` 已有等价实现 |
| **CrewAI** | 角色团队（Crews 自主 / Flows 事件驱动） | 中（约 12%） | 任务输出顺序传递，弱 | 需自行实现 | 弱 | **低**：agent 绑在 crew 生命周期内，不能跨会话常驻 |
| **AutoGen / AG2** | 多智能体对话 | 中（约 9%） | 有限 | 有限 | 无 | **不推荐**：微软战略转向 Microsoft Agent Framework，仅修 bug |
| OpenAI Swarm | 交接模式（handoff） | 低（实验性） | 无 | 无 | 无 | 不适用 |
| Google ADK | 模块化 agent | 中（约 4%） | Vertex AI 集成 | — | — | 不适用：绑定 GCP，与私有化部署冲突 |
| **自定义编排** | 视实现而定 | 约 28% 生产部署 | 视实现 | 视实现 | 视实现 | **高**：本项目已走这条路且基础扎实 |

**关键判断**：本项目 `backend/app/jobs.py` 已实现「job 运行器 + 事件流(seq) + 进度原子提交 + chunk 级 checkpoint + 断电恢复 + 暂停/恢复/删除」，前端已有 WS 断线重连与按 seq 增量补齐。**这正是 LangGraph 的核心价值区间**，引入的边际收益低于生态耦合成本。

### 2.1 五种常见编排模式与框架适配

| 模式 | 说明 | 最佳框架 | 对应本项目 |
|---|---|---|---|
| Router | 分类器把请求路由到专用 agent | LangGraph / 自研 | **标签优先的事件识别（0 LLM）** |
| Planner-Executor | 规划者拆解步骤，执行者执行 | LangGraph | 流程设计师 → 数字人群组 |
| Tool-Using Agent | 单 agent + 工具箱 | 任意 | 写代码数字人 + 工具库 |
| Critic / Verifier Loop | 产出 → 校验 → 循环直到通过 | LangGraph（内建循环检测与最大迭代护栏） | **代码复核人 + 查幻觉回环** |
| Hierarchical | 管理者持有目标，工作者做子任务 | CrewAI | 通路编排官 → 数字人群组 |

---

## 3. 持久执行引擎对比

| 引擎 | 定位 | 关键能力 | 对本项目 |
|---|---|---|---|
| **Temporal** | 通用分布式工作流（durable execution） | 每步状态持久化、崩溃后从中断处续跑、signals/queries、Saga 补偿、human-in-the-loop 原生、MIT 许可可自托管、Python SDK | **备选，优先级低**：现有 chunk 级 checkpoint 已够用；引入需部署 Server（PG + ES），且要求工作流代码严格确定性（见风险 R-1） |
| Apache Airflow | 定时批处理 DAG | 调度/回填生态最大 | 不适用：无 durable execution、无 signal/wait/补偿 |
| Dagster | 资产为中心的数据管道 | 血缘、本地测试 | 不适用：数据工程场景 |
| Prefect | 轻量 Python 流 | 动态流、部署轻 | 不适用：无长时间运行与人工闸门 |
| Camunda 8 | BPM / 人工流程 | BPMN、人工任务 | 备选：若未来要「给非技术人员画流程」，它的 BPMN 建模是最成熟的 |
| Asya（新兴） | 去中心化 actor mesh | **路由是数据不是代码结构**，可在运行时改写 route；每 actor 独立伸缩到零 | **值得关注**：「route 是数据」与本项目「通路库 = 带标签的模板、可沉淀回流」理念一致 |

---

## 4. 协议层现状（2026）

### 4.1 MCP（agent ↔ 工具与数据）

- 2025-12 Anthropic 捐赠给 Linux Foundation 的 Agentic AI Foundation（OpenAI/Google/Microsoft 共同赞助）
- **2026-07-28 第五版规范是史上最大修订**：核心改为**无状态**，移除 `initialize` 握手与 `Mcp-Session-Id`，任何请求可落在任意服务节点；新增 `server/discover`；授权改为 OAuth 2.1 硬化（RFC 9207 `iss` 校验，弃用 DCR 改用 Client ID Metadata Documents）
- 规模：Anthropic 报告 2026-07 月 SDK 下载超 4 亿；registry 约 18,850 条目
- **质量警示**：独立探测发现 **17.2% 的 advertised 远程端点不可达**——生态繁荣 ≠ 生产可用

**对本项目**：工具自生长（《编排示例》§4）需要一个标准的工具描述与调用协议，MCP 是现成的。它是协议不是服务，私有化部署无影响。建议：工具库对外暴露为 MCP server，同时保留内部 `tool_calls.tool_version` 的版本追踪。

### 4.2 A2A（agent ↔ agent）

- **v1.0 于 2026-03-12 发布**，首个稳定版；Linux Foundation 治理，TSC 含 AWS / Cisco / Google / IBM Research / Microsoft / Salesforce / SAP / ServiceNow
- 核心对象是 **Agent Card**：JSON 文档，描述 agent 的身份、能力、端点；v1.0 起支持 JWS 签名（RFC 7515 + RFC 8785 规范化 JSON），调用方可在跨组织委派前验证对方身份
- 交互模型：任务式（send/stream → get/list/cancel），结果为 message 与 artifact；三种绑定 JSON-RPC / gRPC / HTTP+JSON；支持多租户
- 2026-04 已有 150+ 组织生产使用

**对本项目**：跨组织互操作现在用不上，**暂缓**。但 Agent Card 的模式必须借鉴——它就是本项目六元语里 **interface 段**的现成参照（「我能做什么、怎么调用、谁负责」）。

### 4.3 Agent Plugins（打包标准）

2026-08-06 发布 1.0.0，OpenAI / Google / AWS / Cursor / Microsoft / Vercel 六家对齐：MCP 描述符与 Skill 描述符装进同一份 `.well-known/agent-plugin.json` 清单。行业从「协议之争」转入「打包标准之争」。

**对本项目**：数字人的交付格式（待办 `rlwRcA`：本体段导出/导入）可以参考这个打包思路——一份清单同时描述能力（skill）与工具（MCP）。

---

## 5. 最重要的参照：Palantir AIP 的确定性运行时

这是全部调研中与本项目理念最接近、且已被大规模验证的参照。

### 5.1 与本项目铁律的逐条对应

| Palantir AIP | 本项目 | 结论 |
|---|---|---|
| 确定性智能体运行时：全链路可复现、全程留痕审计；交易下发/库存调整/调度指令等关键业务行为交确定性逻辑执行，**仅在意图识别、异常研判等依赖语义解读的环节调用 LLM** | 三条铁律：LLM 只提名、代码裁决、用户终审 | **几乎完全一致** |
| Ontology Studio 把 LLM 定位为「建模加速器」而非「建模替代者」；实体定义、关系分类、权限边界由人工终审，LLM 产出只是待核验草稿 | EDC 提名 → 三关校验 → 用户审批 | 一致 |
| Action 封装 RBAC/CBAC 权限校验，**LLM 不能写自定义数据库代码**，只能触发预校验的安全写回块 | 工具自生长 + L0–L4 能力分级 + 元层禁改 | 一致 |
| 三重校验：LLM 结论 vs 本体结构化数据比对 → 权威数据源佐证 → 人类反馈 | 结构关 / 语义关 / 证据关（span 逐字回原文） | 一致，且本项目证据关更严格 |
| AIP Evals：上线前跑数百个历史数据排列组合，建立显式回归指标 | benchmark 四组对照 + 归因 + 版本管理 | 本项目已有等价物 |

### 5.2 AIP 的五种多智能体协作模式（可直接作为编排原语候选）

| 模式 | 说明 | 对应本项目的场景 |
|---|---|---|
| 链式串行 | 复杂任务拆成连续步骤，**通过本体状态变化传递任务** | 搜综述 → 建数字人 → 造页面 |
| 主从分发 | 主 agent 识别意图，分发给专业子 agent | 通路编排官 → 数字人群组 |
| **执行质检** | 引入质检员角色，依据预设规范审核执行者产出并反馈 | **代码复核人** |
| 异步并行 | 同时唤醒多个 agent 处理独立任务 | 多领域并行调研 |
| 人机协同 | 人类反馈被记录并反哺 AI，形成学习闭环 | 审批闸门 + 反思层 |

### 5.3 AIP Logic 的可视化确定性步骤树

`[输入节点] → [上下文节点（绑定本体对象）] → [LLM 块] → [本体 Action（安全写回）]`

四个节点里只有第三块是 LLM，其余全是确定性代码。**这张图可以直接当作本项目编排图节点类型的参照**——节点分「确定性节点」与「提名节点」两类，而不是所有节点都是 agent。

---

## 6. 对本项目的建议

| 事项 | 建议 | 理由 |
|---|---|---|
| 引入 CrewAI | **否** | agent 绑定 crew 生命周期，无法常驻复用；可观测性与错误恢复弱 |
| 引入 AutoGen | **否** | 微软战略转移，仅修 bug |
| 引入 LangGraph | **可选，倾向否** | 核心价值（checkpoint / HIL / 持久化）已有等价实现；代价是 LangChain 耦合 |
| 引入 Temporal | **暂缓** | 等出现「跑数小时至数天、中途人工挂起」的场景再引入 |
| 采纳 MCP | **是** | 工具自生长需要标准协议；协议非服务，私有化无碍 |
| 采纳 A2A | **暂缓**，但借 Agent Card | 跨组织用不上；Agent Card 正是 interface 段的现成参照 |
| 参考 Palantir AIP | **强烈建议** | 确定性运行时的设计原则与三条铁律逐条对应，且已验证 |
| 编排图节点分类 | **照搬 AIP Logic** | 区分「确定性节点」与「提名节点」，不要所有节点都是 agent |
| 通路库实现方式 | **自研，且 route 作为数据** | 参考 Asya 的「路由是数据不是代码结构」，与「模板沉淀回流」一致 |

### 6.1 真正需要新建的三件事（任何框架都不提供）

1. **通路库与编排图的持久化 + 版本化**（执行成功沉淀为模板、打标签、可回滚）
2. **编排图的确定性校验器**：引用悬空、环检测、基数冲突、交接物类型不匹配、约束自相矛盾
3. **交接物的类型化**：数字人之间传递的不再是自由文本，而是带本体类型与来源指针的结构化对象

---

## 7. 风险与歧义

| 编号 | 内容 | 说明 |
|---|---|---|
| R-1 | **确定性重放与 LLM 的兼容问题** | Temporal 一类引擎要求工作流代码严格确定性（不能随机、不能直接 I/O），LLM 调用必须隔离进 Activity 并记录输入输出以便重放。本项目现有 checkpoint 是 chunk 级，粒度不同，混合使用需重新设计 |
| R-2 | **执行状态快照 ≠ 知识资产版本** | LangGraph 的 checkpoint 是执行状态快照，本项目的 `persona_ontology_versions` 是知识资产版本，两者不可互相替代，也不要共用一张表 |
| R-3 | **MCP 生态质量** | 17.2% 的 advertised 远程端点不可达。采纳协议不等于信任生态里的任意 server，自建工具库仍要走三关校验 |
| R-4 | **框架热度不等于适配度** | 调研普遍结论：框架选择排第四，前三为模型选择、评测基础设施、人工检查点设计。本项目后两项已具备 |
| R-5 | **A2A 的信任边界** | Agent Card 解决「你是谁」，不解决「你说的内容可不可信」。协议不建立语义与内容信任——这恰恰是本体层要补的 |

---

## 8. 参考来源

- OpenAgents：《CrewAI vs LangGraph vs AutoGen vs OpenAgents》（2026-02）
- Presenc AI：《Multi-Agent Orchestration Frameworks 2026》—— 生产占比数据与「框架选择排第四」结论
- Internative：《LangGraph vs CrewAI vs AutoGen: 2026 Comparison》—— 五种架构模式与框架适配
- ai2.work / NeuralCoreTech / it-blue：MCP 与 A2A 协议现状、规范修订与采用数据
- npow.github.io：《The Workflow Orchestration Landscape — March 2026》—— 16 个编排平台象限与各家 tradeoffs
- asya.sh：Temporal / Argo / Airflow / Prefect / Dagster 对比，以及「route 是数据」的去中心化 actor mesh
- Palantir AIP 相关分析（modb / caioweekly / superml / anguklaw）：确定性智能体运行时、Ontology Studio 的 LLM 边界、AIP Logic 步骤树、五种协作模式

---

*调研说明：本文为 2026-09-04 联网调研整理，数据以引用来源为准；生产占比等为第三方估算，方向性参考而非精确统计。*
