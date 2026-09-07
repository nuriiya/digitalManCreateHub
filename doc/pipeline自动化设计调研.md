# pipeline 自动化设计调研（结合已有设计 + 市面公开方案）

> 版本：v1.0（待定夺）
> 日期：2026-09-07
> 触发：数字人编排启动——「流程设计师」数字人需在用户发起对话时设计一个 pipeline 给后台运行
> 关联：`doc/pipeline.md`（通路库 + 编排）、`doc/顶层架构.md`（流程设计师候选）、`doc/pipeline架构调研.md`（框架选型）、`doc/编排示例-调研并写demo页面.md`（首个编排实例）
> 本文只做设计，不写实现代码。

---

## 0. 结论先行（给你的定夺建议）

1. **pipeline 应该是「声明式编排图（数据，JSON/YAML）」，不是代码。** 理由见 §3——它要由 LLM（流程设计师）生成、被确定性校验器把关、被持久化/版本化/打标签复用、被确定性引擎执行。这四点都指向「数据」而非「代码」。
2. **市面 2026 年已收敛到「hybrid」**：声明式拓扑（节点+边写进配置）+ 命令式节点逻辑（节点内部是代码/LLM 调用）。Zylos 2026-07 调研、LangGraph 官方自述、CrewAI Flows 的定位，三条独立来源指向同一结论。
3. **执行引擎复用 `jobs.py`，不引入 LangGraph / Temporal。** `doc/pipeline架构调研.md`（2026-09-04）已下过这个判断：`jobs.py` 已具备 checkpoint/断点续跑/事件流/人工闸门，等价于 LangGraph 核心价值区间；Temporal 等「跑数小时需人工挂起」场景再引入。
4. **真正要自研的是三件事**（任何框架都不提供，`pipeline架构调研.md` §6.1 已列）：
   - 通路库 + 编排图的持久化、版本化、打标签
   - 编排图的**确定性校验器**（环检测 / 引用悬空 / 交接类型不匹配 / 约束自相矛盾）
   - **交接物的类型化**（数字人之间传的不再是自由文本，而是带本体类型 + 来源指针的结构化对象）
5. **「流程设计师」数字人 = 提名器，不裁决。** 它设计出的 pipeline 只是候选图，必须过校验器 + 用户审批才可执行——这与三条铁律完全对齐。

---

## 1. 你要解决的问题（重新表述）

| 需求 | 拆解 |
|---|---|
| 用户发起一个对话（如「调研 X 领域并写个 demo 页面」） | 触发流程设计师 |
| 流程设计师设计「数字人 ↔ 数字人的关系」 | 产出一张编排图（谁先谁后、谁把产出交给谁） |
| 设计出的 pipeline 交给后台运行 | 需要一个确定性执行引擎 + 编排图的数据结构 |

所以 pipeline 的形态，本质是回答一个问题：**「编排图」以什么形态存在、被什么执行？**

---

## 2. 市面 pipeline 自动化的三大流派（2026 现状）

| 流派 | 代表 | 心智模型 | 拓扑怎么表达 | 强项 | 弱项 |
|---|---|---|---|---|---|
| **图优先（命令式）** | LangGraph | 节点 / 边 / 条件边 | `graph.add_node()` + `add_edge()` + `add_conditional_edges()` 显式写代码 | 显式、可调试、checkpoint 持久化、HITL 原生 | 样板多，拓扑和逻辑混在代码里 |
| **事件驱动** | CrewAI Flows | `@start` / `@listen` / `@router` 装饰器 | 拓扑从装饰器注解「涌现」，不必显式连边 | 样板少、贴近人的心智 | agent 绑 crew 生命周期，难常驻复用 |
| **声明式（配置即工作流）** | Kestra / Archon / AgentLoom / Comad World | `workflow:` YAML，agents + steps + input/output | 配置数据，天然可版本化 / 可复用 | 可读、Git 版本化、非程序员可改、可复用模板 | 复杂分支时表达能力受限 |

**三个关键观察**：

1. **Zylos 2026-07 调研**（《Declarative vs Imperative Agent Workflow Orchestration》）把市面划成三阵营——命令式（LangGraph / Claude Agent SDK / OpenAI Agents SDK）、声明式（CrewAI YAML / Google ADK Config / Copilot manifests）、hybrid——并给出结论：**「hybrid 是实际终态：声明式拓扑 + 命令式节点逻辑，复杂度上升时能从 config 平滑升级到 code」**。
2. **LangGraph 官方自述就是 hybrid**：*「节点与边的连接是声明式地做的，节点和边本身不过是 Python/TS 函数」*。拓扑是图 spec，节点内部是任意代码。
3. **声明式 YAML 派（Kestra/Archon/AgentLoom/Comad World）在 2026 明显起势**：核心卖点是「workflow 作为配置，可 Git 版本化、可复用、非程序员可改」——这正好命中 pipeline.md「通路库 = 带标签的可复用模板、执行成功沉淀回流」的诉求。

---

## 3. 为什么「数据」而不是「代码」——对齐你的三条铁律

流程设计师（LLM）设计 pipeline，如果让 LLM **直接生成执行代码**，等于让 LLM 成为「工具行为的决定者」——「LLM 无终审权」当场破功（这正是 `编排示例-调研并写demo页面.md` §4.1 已经指出的冲突，它用的是「工具自生长」同一套逻辑）。

所以 pipeline 必须是**可被确定性代码校验的数据结构**：

```
LLM 生成 pipeline（提名）→ 确定性编排图校验器（裁决）→ 用户审批（终审）→ 入库（版本化）→ 确定性执行引擎跑
```

LLM 只产出「一张图」——节点引用哪些数字人、边怎么连、交接什么——**它完全不接触「怎么执行」**。执行逻辑是写死的确定性代码。这样铁律天然成立，和「工具自生长走编译式固化」同构。

---

## 4. 建议的 pipeline 数据结构（编排图 = 数据）

对齐 A2A 的 Agent Card（= 六元语 interface 段）与 `pipeline架构调研.md` 的「交接物类型化」：

```jsonc
{
  "id": "pl_xxx",
  "name": "调研X领域并写demo页面",
  "version": 1,
  "status": "draft",           // draft → approved(用户终审) → deprecated
  "tags": ["调研", "demo页面"],   // 通路库检索标签
  "entry": "n1",
  "exit": "n6",
  "nodes": [
    {"id": "n1", "persona_id": 2, "kind": "nominate", "step": "设计编排图"},
    {"id": "n2", "persona_id": 5, "kind": "deterministic", "step": "建专业数字人"},
    {"id": "n3", "persona_id": 7, "kind": "nominate", "step": "写demo页面"}
  ],
  "edges": [
    {"from": "n1", "to": "n2", "handoff": {"type": "领域文献集合", "schema": "doc_refs[]"}},
    {"from": "n2", "to": "n3", "handoff": {"type": "部门知识包", "schema": "persona_pkg"}}
  ]
}
```

**关键设计决策（需你定夺，见 §7）**：

- **节点粒度**：一个节点 = 一个数字人的一次完整调用（粗粒度），还是一个数字人的单个 action（细粒度）？
- **交接物（handoff）**：结构化（带本体类型 + 来源指针）还是自由文本？
- **`kind` 节点分类**：沿用 `pipeline架构调研.md` 引用的 Palantir AIP Logic——节点分「确定性节点（deterministic）」和「提名节点（nominate）」，不是所有节点都是 agent。
- **执行引擎**：父 job + N 子 job（复用 `jobs.py` checkpoint / 事件流 / 暂停恢复）。

---

## 5. 三个可选方案（供定夺）

### 方案 A（推荐）—— 声明式编排图 + 确定性校验器 + jobs.py 执行

- pipeline = JSON 数据（§4 结构），存 `pipelines` 表 + `pipeline_nodes` / `pipeline_edges` 表
- 流程设计师 LLM 提名生成图 → 校验器（环/悬空/交接类型/基数）裁决 → 用户审批 → 版本化
- 执行：现有 `jobs.py` 加一个 `kind='pipeline'` 的执行器（父 job 依次/并发调度子 job，子 job 复用现有 ingest/ontology/chat 执行路径）
- **自研量**：pipelines 表 + 校验器 + pipeline 执行器 + 前端编排图预览/审批 UI
- **优点**：完全对齐三条铁律与既有架构，零新框架依赖，复用 benchmark 版本管理那套成熟机制
- **缺点**：执行器要自己写（但 jobs.py 底子已打好，主要是「按图调度」的胶水）

### 方案 B —— 引入 LangGraph 作执行引擎

- pipeline 图直接映射为 LangGraph `StateGraph`（节点=数字人调用、边=交接、条件边=分支）
- checkpoint/HITL/持久化由 LangGraph 提供
- **优点**：省去自研执行器，分支/循环/暂停能力现成
- **缺点**：引入 LangChain 生态耦合（`pipeline架构调研.md` 已判断「可选但收益有限」）；与现有 PG 直连的 jobs/事件流要桥接；且 pipeline 数据要转成 LangGraph 图，多一层映射

### 方案 C —— CrewAI Flows 事件驱动

- `@start` / `@listen` 装饰器组织编排
- **缺点（已否）**：agent 绑 crew 生命周期、不能跨会话常驻复用，与「数字人常驻复用」直接冲突；且事件驱动拓扑对「确定性校验器」不友好（拓扑涌现，难静态校验环/悬空）

---

## 6. 与「流程设计师」数字人的关系

「流程设计师」是顶层架构.md §4 的候选**基础数字人**（不是 Function）。它的六元语：

| 段 | 内容 | 现状 |
|---|---|---|
| identity | 查通路库、设计编排图、提名所需数字人与工具 | 待建（与其它基础数字人一样走 Spec 编译路径） |
| ontology | 通路 / 编排图 / 数字人 / 交接 Link | 待建（依赖 §5 的 pipelines 表） |
| actions | 查通路库、提名编排图、沉淀模板 | 待建 |
| guardians | 编排图校验器（环/悬空/交接类型） | 待建（这是 N3 校验器） |
| interface | 编排图预览 + 审批卡 | 待建 |
| reflection | 复盘编排失败 → 优化模板 | 待建 |

它设计 pipeline 的调用链（0 额外 LLM 裁决）：

```
用户对话 → 流程设计师(LLM) 提名编排图 → 校验器(代码) 裁决 → 用户(终审) 批准 → 执行引擎(jobs.py) 跑 → 成功沉淀为通路模板
```

---

## 7. 需要你定夺的问题（汇总）

| # | 决策点 | 选项 | 我的建议 |
|---|---|---|---|
| P1 | pipeline 形态 | 数据(JSON 图) / 代码 / 混合 | **数据（方案 A）** |
| P2 | 执行引擎 | 自研(复用 jobs.py) / LangGraph / CrewAI | **自研（方案 A）** |
| P3 | 节点粒度 | 数字人级 / 动作级 / 先粗后细 | **先数字人级，动作级留增强** |
| P4 | 交接物 | 结构化(类型+来源指针) / 自由文本 | **结构化** |
| P5 | 通路库检索 | 标签优先 0 LLM（复用事件识别思路）/ LLM 兜底 | **标签优先** |
| P6 | 编排图是否允许分支/循环/并发 | 仅 DAG / 支持循环(≤N 轮) / 支持并发 | **先 DAG + 并发，循环靠「复核打回」这种外部重触发** |

---

*生成说明：本文为 v1.0 调研稿。市面方案数据来源：Zylos 2026-07 调研、CrewAI 官方迁移指南、Catalyst&Code / DevelopersDigest / CodeBridge 2026 框架对比、Kestra / Archon / AgentLoom / Comad World 声明式方案。已有设计依据：doc/pipeline.md、doc/顶层架构.md、doc/pipeline架构调研.md、doc/编排示例-调研并写demo页面.md。*
