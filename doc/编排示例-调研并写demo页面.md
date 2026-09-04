# 编排示例：调研某领域并写一个 demo 页面（v1.0）

> 版本：v1.0 · 2026-09-03
> 状态：**已确认主要决策**（工具自生长走编译式固化；写代码数字人 = 基础骨架 + 部门技能包）
> 定位：pipeline.md §1/§2「通路库未命中 → 创建通路 → 数字人编排」的**第一个完整实例**，也是 `doc/顶层架构.md` 基础/专业二分的**首个压力测试**。
> 本文只做设计，不写实现代码。图示为深色版，配色语义沿用 pipeline.md v5：蓝=基础数字人、绿=专业数字人（任务中动态创建）、橙=LLM 提名/工具自生长、灰=任务态。

---

## 0. 场景与它验证什么

**任务**：「调研 X 领域并写一个 demo 页面」。通路库无现成模板 → 创建通路 → 拉起数字人群组协作。

这个场景一次性压出了体系里四件此前只有设计、没有实现路径的事：

| # | 验证点 | 出处 |
|---|---|---|
| V1 | 通路库未命中时，由数字人（而非人）设计编排图 | pipeline.md §1/§2 |
| V2 | **专业数字人在任务过程中被动态创建** | 顶层架构.md §0/§5 |
| V3 | **工具自生长**：任何一步缺工具就造工具 | 本文新增，§4 |
| V4 | 幻觉回环：产出的页面要被回查有没有编造 | 复用 quiz/exam 判分与 benchmark 判卷 |

---

## 1. 编排总图

<svg viewBox="0 0 680 636" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<title>编排示例：调研某领域并写一个 demo 页面</title>
<desc>任务进来后由流程设计师用本体设计编排图，依次执行搜索领域综述、建立专业领域数字人、由专业数字人与写代码数字人共同产出页面、代码复核人复核功能、专业数字人查幻觉五个步骤，最后交付用户并沉淀为模板。右侧是工具自生长泳道：任何一步发现缺少工具，都由写代码数字人生成工具，经沙箱测试与用户审批后固化入工具库。</desc>
<defs>
<marker id="arrowEx" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
<path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</marker>
</defs>
<text x="340" y="24" text-anchor="middle" font-size="14" font-weight="500" fill="#F1EFE8">编排示例：调研 X 领域并写一个 demo 页面</text>

<rect x="40" y="44" width="370" height="44" rx="8" fill="#444441" stroke="#B4B2A9" stroke-width="0.5"/>
<text x="225" y="66" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#F1EFE8">任务：调研 X 领域并写一个 demo 页面</text>
<path d="M225 88 V114" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="116" width="370" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="56" y="136" font-size="13" font-weight="500" fill="#E6F1FB" dominant-baseline="central">流程设计师（基础数字人）</text>
<text x="56" y="154" font-size="11" fill="#B5D4F4" dominant-baseline="central">查通路库未命中 → 用本体设计编排图 → 用户确认</text>
<path d="M225 168 V186" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="188" width="370" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="56" y="208" font-size="13" font-weight="500" fill="#E6F1FB" dominant-baseline="central">① 搜索相关领域综述</text>
<text x="56" y="226" font-size="11" fill="#B5D4F4" dominant-baseline="central">产出原料：领域文献 → 交给建数字人工具</text>
<path d="M225 240 V258" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="260" width="370" height="52" rx="8" fill="#085041" stroke="#9FE1CB" stroke-width="0.5"/>
<text x="56" y="280" font-size="13" font-weight="500" fill="#E1F5EE" dominant-baseline="central">② 建立专业领域数字人</text>
<text x="56" y="298" font-size="11" fill="#9FE1CB" dominant-baseline="central">调建数字人工具：RAG + 本体分解 → 知识包 → 审批</text>
<path d="M225 312 V330" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="332" width="370" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="56" y="352" font-size="13" font-weight="500" fill="#E6F1FB" dominant-baseline="central">③ 专业数字人 + 写代码数字人</text>
<text x="56" y="370" font-size="11" fill="#B5D4F4" dominant-baseline="central">领域知识供给 + 页面实现 → demo 页面</text>
<path d="M225 384 V402" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="404" width="370" height="52" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="56" y="424" font-size="13" font-weight="500" fill="#E6F1FB" dominant-baseline="central">④ 代码复核人（基础数字人）</text>
<text x="56" y="442" font-size="11" fill="#B5D4F4" dominant-baseline="central">复核功能正确性 · 不通过 → 回写代码数字人</text>
<path d="M225 456 V474" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="476" width="370" height="52" rx="8" fill="#085041" stroke="#9FE1CB" stroke-width="0.5"/>
<text x="56" y="496" font-size="13" font-weight="500" fill="#E1F5EE" dominant-baseline="central">⑤ 专业数字人查幻觉</text>
<text x="56" y="514" font-size="11" fill="#9FE1CB" dominant-baseline="central">页面陈述 vs 证据 span 逐字比对 · 有幻觉 → 回③</text>
<path d="M225 528 V546" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>

<rect x="40" y="548" width="370" height="44" rx="8" fill="#444441" stroke="#B4B2A9" stroke-width="0.5"/>
<text x="225" y="570" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#F1EFE8">交付用户 + 通路沉淀为可复用模板</text>

<path d="M410 142 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>
<path d="M410 214 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>
<path d="M410 286 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>
<path d="M410 358 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>
<path d="M410 430 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>
<path d="M410 502 H428" fill="none" stroke="#FAC775" stroke-width="1" stroke-dasharray="4 3"/>

<rect x="430" y="116" width="210" height="412" rx="20" fill="#412402" stroke="#FAC775" stroke-width="0.5"/>
<text x="450" y="140" font-size="13" font-weight="500" fill="#FAC775">工具自生长泳道</text>
<rect x="450" y="156" width="170" height="64" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="535" y="178" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">缺工具检测</text>
<text x="535" y="198" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">确定性代码判定 · 0 LLM</text>
<path d="M535 220 V234" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>
<rect x="450" y="236" width="170" height="64" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="535" y="258" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">写代码数字人生成工具</text>
<text x="535" y="278" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">产出只是提名，不是成品</text>
<path d="M535 300 V314" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowEx)"/>
<rect x="450" y="316" width="170" height="64" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="535" y="338" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#FAEEDA">沙箱测试 + 用户审批</text>
<text x="535" y="358" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#FAC775">通过后版本化固化入工具库</text>
<text x="450" y="404" font-size="11" fill="#FAC775">任何一步缺工具都走这条泳道</text>
<text x="450" y="424" font-size="11" fill="#FAC775">运行时只调用已固化的版本</text>
<text x="450" y="444" font-size="11" fill="#FAC775">不使用 LLM 的即时输出</text>
<text x="340" y="614" text-anchor="middle" font-size="12" fill="#888780">蓝色 = 基础数字人 · 绿色 = 专业数字人（任务中动态创建）· 橙色 = 工具自生长</text>
</svg>

---

## 2. 参与者清单

| 角色 | 类别 | ontology 段 | 本任务中的职责 | 来源 |
|---|---|---|---|---|
| 流程设计师 | 基础 | 通路 / 编排图 / 数字人 / 交接 Link | 查通路库、设计编排图、提名所需数字人与工具 | 本文新增候选 |
| 专业领域数字人（X 领域） | **专业（任务中创建）** | X 领域术语 / 概念 / 最佳实践 | 供给领域知识；最后回查页面有没有幻觉 | 顶层架构.md §5 |
| 写代码数字人 | 基础骨架 + 部门技能包 | 代码结构 / 框架 API / 页面元素 | 实现页面；兼任「工具工厂」生成缺的工具 | 本文新增候选 |
| 代码复核人 | 基础 | 代码 / 测试用例 / 缺陷 / 功能点 | 复核功能正确性，不通过打回 | 本文新增候选 |
| 建数字人工具 | **Function（非数字人）** | — | 把 `rag_prototype` 整条管线封装成可编排调用的确定性工具 | 本文新增 |
| 工具工厂编译器 | **Function（非数字人）** | — | 沙箱执行 + UT + 三关校验；产物版本化固化 | 本文新增 |

> 判据复核：三关校验、工具工厂编译器、建数字人管线都属 **Function**（确定性、可重放、零 LLM），不包装成数字人。写代码数字人之所以是数字人，是因为它会**提名**代码与工具、会**反思**（复盘生成失败模式），而不只是执行。

---

## 3. 逐步拆解

| 步 | 谁 | 输入 | 输出 | 用到什么工具 | 失败出口 | LLM |
|---|---|---|---|---|---|---|
| ① 搜综述 | 流程设计师调度 | X 领域名 | 领域文献集合 | 检索工具（缺则造） | 检索无果 → 向用户要语料 | 无（调度） |
| ② 建专业数字人 | 建数字人工具 | 领域文献 | X 领域专业数字人（pending） | **建数字人工具**（ingest→EDC→三关→装配） | 文献不足 → 回①；本体候选审批被否 → 回①补料 | 提名（V4-Flash + GLM） |
| ③ 造页面 | 专业数字人 + 写代码数字人 | 知识包 + 页面需求 | demo 页面草稿 | 渲染/构建工具（缺则造） | 构建失败 → 写代码数字人修 | 提名 |
| ④ 复核功能 | 代码复核人 | 页面草稿 + 功能点清单 | 复核报告 / 打回 | 测试运行器（缺则造） | 不通过 → 回③（≤2 轮） | 提名 |
| ⑤ 查幻觉 | 专业数字人 | 页面草稿 + 证据 span | 幻觉清单 / 通过 | 现有 quiz/exam 判分 + benchmark 判卷 | 发现幻觉 → 回③修正或删除该陈述 | 提名（判卷）+ 确定性终审 |
| ⑥ 交付 | 系统 | 通过后的页面 | 交付物 + 通路模板 | — | — | 无 |

**② 是关键**：它把 `rag_prototype` 现有全链路（文档 → 分段 → 摘要/标签 → 嵌入 → EDC 抽取 → 三关校验 → 装配 → 审批）封装成一个确定性工具，让「造数字人」第一次成为可被编排调用的一步。这也让 pipeline.md §2 的「未命中 → 创建通路」有了具体实现路径。

---

## 4. 工具自生长：编译式固化（本文核心）

### 4.1 冲突：工具自生长 vs Function 的定义

五原语里 **Function = 确定性护栏（无 LLM、无状态、可重放）**，它是 guardians 的一部分。而「缺工具就让写代码数字人（LLM）造一个」，如果运行时直接执行 LLM 即时写出的代码，那么 LLM 就成了工具行为的决定者——**「LLM 无终审权」当场破功**，guardians 的可靠性从根上被稀释。

### 4.2 解法：编译式固化（已确认）

工具不是「运行时临时跑一段 LLM 代码」，而是走一遍**迷你工厂流水线**：

| 步 | 做什么 | LLM | 出口 |
|---|---|---|---|
| ① 生成 | 写代码数字人产出工具代码 + 声明（名称/参数/能力级别/失败路径） | 提名 | 声明不全 → 直接丢弃 |
| ② 沙箱执行 + UT | 沙箱里跑自带测试用例，超时/越权立即终止 | 无 | 测试不过 → 回①（≤2 轮） |
| ③ 三关校验 | 结构（声明完整/参数闭集）· 语义（能力级别与声明一致）· 证据（高危能力必须人工写，生成的一律拒绝） | 无 | 不过 → 拒绝 |
| ④ 用户审批 | 低风险自动（L0/L1）· 高风险人工（L2） | 无 | 拒绝 → 回① |
| ⑤ 版本化固化 | 打版本号入工具库，之后它就是普通 Function | 无 | 上线 vN |

**固化后它与手写 Function 地位完全相同**：可测试、可重放、可版本回滚。铁律不破——**运行时调用的每一行代码都是某个确定版本的编译产物，不是 LLM 的即兴输出**。

这与「数字人是编译出来的」（顶层架构.md §2）**完全同构**：工具也该是编译出来的。差别只在编译器的输入是 Spec 还是代码提名。

**代价（必须接受）**：缺工具时任务要等一轮固化的时间，不能即时用上。这是「绝对准确」换来的，不能省。

### 4.3 三条护栏（硬性，需 UT 强制）

1. **禁止递归自举**：造工具的过程中不允许再触发造工具，递归深度上限 = 1。否则「造工具需要工具，于是再造一个」会无限递归。
2. **元层禁改**：写代码数字人**不能**生成「修改工具库 / 修改本体库 / 修改 guardians / 修改审批规则」的工具。这条是防止数字人被约束卡住后，自己造一个绕过约束的工具——那会让铁律从内部被击穿。
3. **高危能力黑名单**：见下表 L3/L4，永不生成，只能人工写。

### 4.4 能力分级清单

| 级别 | 能力 | 可否生成 | 审批 |
|---|---|---|---|
| L0 只读 | 检索、读取、查询 | 可 | 低风险自动 |
| L1 纯计算 | 统计、转换、格式化 | 可 | 低风险自动 |
| L2 本地写入 | 写文件、写临时产物（限沙箱路径） | 可（路径硬限制） | **人工** |
| L3 网络 | 外部 API、抓取 | **禁止生成**，人工写 | 人工 + 安全评审 |
| L4 权限 / 凭证 | 读写凭证、改权限、改本体库、改 guardians | **绝对禁止生成** | — |

生成工具的默认能力边界 = **只读 + 纯计算 + 格式化**。写入类操作只能复用已有工具。

### 4.5 数据模型增量（建议）

```
tools(id, name, version, spec_json, code, capability_level ∈ {L0,L1,L2,L3,L4},
      generated_by ∈ {human, code_person}, source_nomination_id,
      tests_json, state ∈ {pending, approved, deprecated}, created_at)
tool_versions(id, tool_id, version, snapshot_json, changelog, created_at)   -- 快照 + 回滚，复用 persona_ontology_versions 模式
tool_calls(id, job_id, tool_id, tool_version, args_json, result_json, ok, created_at)   -- 可追溯：每次调用记版本号
```

关键字段是 `tool_calls.tool_version`——**任何一次工具调用都能追溯到确定版本**，否则「可重放」是句空话。

---

## 5. 幻觉回环：复用现有判分机制（不必从零设计）

第⑤步「专业数字人查幻觉」可以完全复用两处既有机制：

1. **quiz / exam_results 判分**（`backend/app/ontology.py` run_exam）：把页面里每条**事实性陈述**当作一道题，要求给出 `quote`（原文 span）+ `used`（用到的本体名）。判分规则是确定性的：quote 必须逐字命中 chunk 原文，used 必须命中候选闭集，答案须含预期答案。
2. **benchmark 判卷**（`backend/app/benchmark.py`）：GLM 提名 verdict ∈ {correct, partial, wrong, refused} → 确定性终审（闭集校验 + 拒答词检测）→ 统计幻觉率。

| verdict | 含义 | 处理 |
|---|---|---|
| correct | 陈述有原文支撑 | 通过 |
| partial | 部分支撑 | 标黄，提示用户 |
| wrong | 与原文冲突或 quote 造假 | **回③修正或删除该陈述** |
| refused | 本体/证据不足，无法判定 | 显式挂起，标「未验证」 |

原则不变：**verdict 只是提名，确定性代码终审，剔除/批准由用户拍板**。页面里不允许出现「未验证」却被写成事实的陈述。

---

## 6. 失败路径总表

| 环节 | 失败情形 | 出口 |
|---|---|---|
| ① 搜综述 | 检索无果 | 向用户索要语料，不猜测 |
| ② 建数字人 | 文献不足 / 本体候选全被否 | 回①补料；仍不足则显式挂起，不造半成品数字人 |
| ③ 造页面 | 构建失败 | 写代码数字人修，≤2 轮；仍失败转人工 |
| ③ 造页面 | 缺工具 | 走工具自生长泳道；固化被拒 → 显式挂起该能力 |
| ④ 复核 | 功能不通过 | 回③，≤2 轮；超限转人工 |
| ⑤ 查幻觉 | 发现 wrong | 回③；无法修正则**删除该陈述**，宁缺勿编 |
| ⑤ 查幻觉 | refused | 标「未验证」，不下事实结论 |
| 全程 | 任一环节 LLM 不可达 | 该环节显式挂起并告知用户，不静默降级 |

---

## 7. 与本仓库代码的衔接

**可直接复用**：

- `ingest.py` / `ontology.py` / `assembly.py`：整条建数字人链路 → 封装为「建数字人工具」
- `identity.py` + `anchors`：专业数字人的身份与锚点
- `ontology.py` run_exam + `benchmark.py`：幻觉回环与判卷
- `jobs.py` 运行器 + 事件流 + checkpoint：群组执行（父 job + N 子 job）
- `chat.py` 动态检索窗口：专业数字人被问到时的本体约束
- `persona_ontology_versions`：工具版本化的现成模板

**需新建**：

| # | 事项 | 说明 |
|---|---|---|
| T1 | 建数字人工具（Function 封装） | 把现有管线包成幂等、可重放、带版本输出的工具 |
| T2 | 工具库 + 工具工厂编译器 | 沙箱执行 + UT + 三关 + 审批 + 版本化；**本文唯一无现成设计可依的部分** |
| T3 | 通路库 + 编排执行引擎 | 待办 `rsE0kc` 及其三个子任务 |
| T4 | 三个新增基础数字人 | 流程设计师 / 写代码数字人（含技能包挂载位）/ 代码复核人 |
| T5 | 能力分级清单的强制校验 | L3/L4 永不生成，需 UT 兜底 |

---

## 8. 待定问题

| 编号 | 问题 | 备注 |
|---|---|---|
| B-1 | 流程设计师设计编排图时，LLM 提名到什么粒度 | 只提名「需要哪些数字人」，还是连「谁先谁后、交接什么」一起提名？后者 LLM 权限更大 |
| B-2 | 工具固化的等待时间如何与用户体验平衡 | 是否允许「先用降级路径继续，工具固化后补跑」 |
| B-3 | 生成的工具算不算数字人的 reflection 素材 | 工具失败率是否回流为写代码数字人的改进提名 |
| B-4 | 页面这类非结构化产物，如何定义「陈述」的粒度 | 整句 / 段落 / 数据点，直接影响幻觉回环的可判定性 |

---

*生成说明：本文为 v1.0，从 2026-09-03 的用户场景讨论整理。主要决策已确认（编译式固化、写代码数字人 = 基础骨架 + 部门技能包）；§8 四项待定不影响整体架构，可在实现阶段收敛。*
