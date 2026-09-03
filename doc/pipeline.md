# AI Pipeline 流程图（v5 · 事件模板化 + 数字人编排 + 数字人创建流水线）

> 核心模型：**任务进来 → 查通路库 → 命中套用 / 未命中创建通路 → 通路 = 数字人编排（找数字人 → 编关系 → 编协作流程 + 加约束）→ 执行 → 通路迭代**
>
> 数字人 = **六元语封装体**（identity / ontology / actions / guardians / interface / reflection），参考 Palantir 模式但不拘泥其原生五元，按项目自身需求扩展。
>
> 本文件为单文件 Markdown，图均为**内联 SVG 矢量图**，放大不失真；建议用 Typora / Obsidian / VS Code 打开。

---

## 1. 系统总览：事件模板化主流程

用户发来一个请求，系统先去通路库检索有没有现成通路能立马套用；命中则直接套用，未命中则进入「创建通路」流程。通路的核心是**数字人编排**（不是步骤位点序列），执行成功后沉淀为新模板、打标签回流。

<svg viewBox="0 0 680 734" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrowS" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <rect x="270" y="40" width="140" height="44" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="62" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">任务进来</text>
  <line x1="340" y1="84" x2="340" y2="108" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="240" y="108" width="200" height="56" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="340" y="130" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#444441">通路库 · 带标签的肌肉</text>
  <text x="340" y="148" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">所有模板已打标签</text>
  <line x1="340" y1="164" x2="340" y2="196" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="240" y="196" width="200" height="56" rx="8" fill="#EAF3DE" stroke="#3B6D11" stroke-width="0.5"/>
  <text x="340" y="218" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#27500A">展示标签选项给用户</text>
  <text x="340" y="236" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#3B6D11">0 次 LLM 调用</text>
  <line x1="340" y1="252" x2="170" y2="300" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <line x1="340" y1="252" x2="510" y2="300" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <text x="340" y="276" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#633806">用户选择？</text>
  <rect x="80" y="300" width="180" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="170" y="322" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#085041">点选标签</text>
  <text x="170" y="340" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">命中模板</text>
  <rect x="420" y="300" width="180" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="510" y="322" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#3C3489">未选择</text>
  <text x="510" y="340" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#534AB7">LLM 判断数字人</text>
  <line x1="170" y1="356" x2="170" y2="378" stroke="#888780" stroke-width="1.5"/>
  <line x1="510" y1="356" x2="510" y2="378" stroke="#888780" stroke-width="1.5"/>
  <line x1="170" y1="378" x2="340" y2="400" stroke="#888780" stroke-width="1.5"/>
  <line x1="510" y1="378" x2="340" y2="400" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="210" y="400" width="260" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="422" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">读取编排图</text>
  <text x="340" y="440" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#185FA5">数字人 · 先后 · 交接</text>
  <line x1="340" y1="456" x2="340" y2="486" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="210" y="486" width="260" height="56" rx="8" fill="#EAF3DE" stroke="#3B6D11" stroke-width="0.5"/>
  <text x="340" y="508" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#27500A">展示编排图给用户确认</text>
  <text x="340" y="526" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#3B6D11">确认后执行</text>
  <line x1="340" y1="542" x2="340" y2="572" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="210" y="572" width="260" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="594" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">调度数字人执行</text>
  <text x="340" y="612" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#185FA5">各自走内部流程</text>
  <line x1="340" y1="628" x2="340" y2="658" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowS)"/>
  <rect x="210" y="658" width="260" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="680" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#3C3489">沉淀为新模板</text>
  <text x="340" y="698" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#534AB7">打标签 · 入库可复用</text>
  <path d="M470 686 C 570 686, 570 136, 442 136" fill="none" stroke="#534AB7" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#arrowS)"/>
  <text x="555" y="400" text-anchor="middle" font-size="11" fill="#534AB7">执行成功 → 打标签回流</text>
</svg>

---

## 2. 创建通路的四步（未命中时）

用户没选标签、且 LLM 判断无现成通路时，进入创建通路流程。四步按序执行，每一步都是**声明式产物**，固定化、可追溯、可版本化。

<svg viewBox="0 0 680 500" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrowC" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <rect x="210" y="40" width="260" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="62" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">第一步：找可用的数字人</text>
  <text x="340" y="80" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#185FA5">复用已有数字人，缺则触发创建</text>
  <line x1="340" y1="96" x2="340" y2="124" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowC)"/>
  <rect x="210" y="124" width="260" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="146" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#085041">第二步：编写数字人 ↔ 数字人关系</text>
  <text x="340" y="164" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">谁能连谁 · 基数 · 产出交接（Link）</text>
  <line x1="340" y1="180" x2="340" y2="208" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowC)"/>
  <rect x="210" y="208" width="260" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="230" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#085041">第三步：编写协作流程 + 加约束</text>
  <text x="340" y="248" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">谁先谁后 · 流程内嵌约束规则</text>
  <line x1="340" y1="264" x2="340" y2="292" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowC)"/>
  <rect x="210" y="292" width="260" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="314" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#3C3489">第四步：通路迭代</text>
  <text x="340" y="332" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#534AB7">执行后反思 → 洞察 → 版本升级</text>
  <line x1="340" y1="348" x2="340" y2="376" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowC)"/>
  <rect x="210" y="376" width="260" height="56" rx="8" fill="#EAF3DE" stroke="#3B6D11" stroke-width="0.5"/>
  <text x="340" y="398" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#27500A">沉淀为模板 · 打标签入库</text>
  <text x="340" y="416" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#3B6D11">下次同类请求直接套用</text>
  <path d="M470 404 C 560 404, 560 68, 472 68" fill="none" stroke="#534AB7" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#arrowC)"/>
  <text x="578" y="236" text-anchor="middle" font-size="11" fill="#534AB7">迭代回流</text>
</svg>

---

## 3. 数字人 = 六元语封装体

每个数字人由**六元语**定义，六元语内容各不相同、反思各自独立。ontology 段就是数字人的实体关系图（它能碰哪些实体、实体之间怎么连）。

<svg viewBox="0 0 680 300" width="100%" xmlns="http://www.w3.org/2000/svg">
  <text x="40" y="30" font-size="13" font-weight="500" fill="#444441">数字人 = 六元语封装体</text>
  <rect x="40" y="48" width="112" height="44" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="96" y="70" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">identity</text>
  <rect x="160" y="48" width="112" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="216" y="70" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">ontology</text>
  <rect x="280" y="48" width="112" height="44" rx="8" fill="#EAF3DE" stroke="#3B6D11" stroke-width="0.5"/>
  <text x="336" y="70" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#27500A">actions</text>
  <rect x="400" y="48" width="112" height="44" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="456" y="70" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#633806">guardians</text>
  <rect x="520" y="48" width="112" height="44" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="576" y="70" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">interface</text>
  <rect x="320" y="112" width="180" height="44" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="410" y="134" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">reflection（第六元）</text>
  <text x="96" y="108" text-anchor="middle" font-size="11" fill="#5F5E5A">是谁</text>
  <text x="216" y="108" text-anchor="middle" font-size="11" fill="#5F5E5A">作业世界（实体关系图）</text>
  <text x="336" y="108" text-anchor="middle" font-size="11" fill="#5F5E5A">会做什么</text>
  <text x="456" y="108" text-anchor="middle" font-size="11" fill="#5F5E5A">不能越过什么</text>
  <text x="576" y="108" text-anchor="middle" font-size="11" fill="#5F5E5A">对外暴露什么</text>
  <rect x="40" y="180" width="600" height="100" rx="10" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="60" y="204" font-size="12" font-weight="500" fill="#444441">reflection（自我反思 · 自我迭代）——每个数字人独立，不共享</text>
  <text x="60" y="226" font-size="11" fill="#5F5E5A">每个数字人负责的事不同，反思内容不同；数字人单独迭代，不是每做完一件事就迭代</text>
  <text x="60" y="244" font-size="11" fill="#5F5E5A">达到触发条件才复盘，产出洞察 → 分级审批 → 回流自身 Spec（禁运行时热更新）</text>
  <text x="60" y="262" font-size="11" fill="#5F5E5A">RAG：为数字人搭建「先验知识 + 边界」，可随时替换、任务中持续增长，创建时注入</text>
</svg>

---

## 4. 示例：创建 APP 需求文档（拆解 4 个数字人）

以「用户要写一个记账 APP 的需求文档」为例，查通路库未命中 → 创建通路 → 识别出 **4 个数字人**，编排协作完成。

<svg viewBox="0 0 680 600" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrowA" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <rect x="230" y="40" width="220" height="44" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="62" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">用户请求：做一个记账 APP 的需求文档</text>
  <line x1="340" y1="84" x2="340" y2="108" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <rect x="230" y="108" width="220" height="56" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="340" y="130" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#444441">查通路库</text>
  <text x="340" y="148" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">无现成「写 APP 需求」模板 → 未命中</text>
  <line x1="340" y1="164" x2="340" y2="188" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <rect x="230" y="188" width="220" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="210" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#3C3489">创建通路 · 第一步：找数字人</text>
  <text x="340" y="228" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#534AB7">识别出需要 4 个数字人</text>
  <line x1="340" y1="244" x2="340" y2="268" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <rect x="40" y="268" width="145" height="64" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="112" y="292" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">① 需求分析师</text>
  <text x="112" y="312" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">主 · 对齐+提名</text>
  <rect x="195" y="268" width="145" height="64" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="267" y="292" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">② 领域专家</text>
  <text x="267" y="312" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">APP 先验知识</text>
  <rect x="350" y="268" width="145" height="64" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="422" y="292" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#633806">③ 合规守护官</text>
  <text x="422" y="312" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">合规/安全/隐私</text>
  <rect x="505" y="268" width="135" height="64" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="572" y="292" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">④ 文档架构师</text>
  <text x="572" y="312" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">组装 PRD</text>
  <line x1="340" y1="332" x2="340" y2="356" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <text x="340" y="376" text-anchor="middle" font-size="13" font-weight="500" fill="#444441">协作编排（谁先谁后、谁把产出交给谁）</text>
  <rect x="40" y="392" width="240" height="60" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="160" y="414" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">① 需求分析师</text>
  <text x="160" y="434" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">提问 · 对齐 · 提名需求条目</text>
  <rect x="400" y="392" width="240" height="60" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="520" y="414" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">② 领域专家</text>
  <text x="520" y="434" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">补领域知识 → 回填条目</text>
  <line x1="280" y1="422" x2="400" y2="422" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <rect x="40" y="480" width="240" height="60" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="160" y="502" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#633806">③ 合规守护官</text>
  <text x="160" y="522" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">约束注入 · 越界告警</text>
  <rect x="400" y="480" width="240" height="60" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="520" y="502" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">④ 文档架构师</text>
  <text x="520" y="522" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">组装 → 渲染 → 导出 PRD</text>
  <line x1="160" y1="452" x2="160" y2="480" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <line x1="520" y1="452" x2="520" y2="480" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <line x1="280" y1="510" x2="400" y2="510" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowA)"/>
  <rect x="230" y="560" width="220" height="36" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="578" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#3C3489">执行成功 → 沉淀为「写 APP 需求」模板</text>
</svg>

---

## 5. 四个数字人的六元语结构

| # | 数字人 | identity（是谁） | ontology（作业世界） | actions（会做什么） | guardians（不能越过） | interface（对外暴露） | reflection（自我迭代） |
|---|---|---|---|---|---|---|---|
| ① | 需求分析师 | 把模糊意图对齐成结构化需求条目；不裁决冲突/不扩充需求/不改优先级 | 需求条目、澄清请求、提名轨迹 | 提问、提名、应答澄清 | G-01~G-08 三关 | 需求条目清单、澄清对话 | 复盘提问命中率 → 优化提问模板 |
| ② | 领域专家 | 补齐 APP 领域先验知识；只补知识、不替用户拍板 | 领域概念、术语、最佳实践（RAG 来源） | 知识检索、术语解释、回填条目 | 知识须有来源、无来源标「未知」 | 知识卡片、术语表 | 复盘知识缺口 → 反哺 RAG |
| ③ | 合规守护官 | 合规/安全/隐私/权限边界把关；只拦不改 | 约束规则、合规条款、权限边界 | 合规检查、约束注入、越界告警 | 硬约束不可被绕过 | 合规检查报告、越界告警 | 复盘漏检案例 → 扩充规则库 |
| ④ | 文档架构师 | 把裁决通过的条目组装成规范 PRD；零新增事实 | 需求文档、需求条目、文档模板 | 组装、渲染、导出 | 文档零新增、越界即阻断 | 文档预览、导出 | 复盘文档结构 → 优化模板 |

---

## 6. 数字人分类：平台级通用 vs 部门级领域

数字人分**两类**（暂不设第三类，避免过度设计）：

| 维度 | 平台级通用数字人 | 部门级领域专家 |
|---|---|---|
| 谁提供/维护 | 平台方 | 各部门 |
| 例子 | 需求分析师、文档架构师、合规守护官 | 财务/HR/IT/法务专家 |
| ontology | 通用本体（需求条目/文档/轨迹） | 部门专属本体（术语/规则，RAG 来源） |
| 反思回流 | 平台层 · 全局生效 | 部门层 · 部门内生效 |

**部门的交付物 = 领域专家**：部门只交「部门文档 + 知识包」，平台用领域专家模板克隆生成，部门无需懂六元语。

<svg viewBox="0 0 680 500" width="100%" xmlns="http://www.w3.org/2000/svg">
  <title>数字人分类：平台级通用 vs 部门级领域</title>
  <desc>平台级通用数字人由平台方提供维护跨部门复用，部门级领域专家由各部门提供，通过领域专家模板加部门知识包合成。</desc>
  <text x="40" y="30" font-size="14" font-weight="500" fill="#444441">数字人分类：平台级通用 vs 部门级领域</text>
  <rect x="40" y="46" width="600" height="138" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="60" y="70" font-size="13" font-weight="500" fill="#0C447C">平台级通用数字人 —— 平台方提供 + 维护，跨部门复用</text>
  <rect x="60" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#378ADD" stroke-width="0.5"/>
  <text x="148" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">需求分析师</text>
  <rect x="252" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#378ADD" stroke-width="0.5"/>
  <text x="340" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">文档架构师</text>
  <rect x="444" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#378ADD" stroke-width="0.5"/>
  <text x="532" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">合规守护官</text>
  <text x="60" y="152" font-size="11" fill="#185FA5">通用本体：需求条目 / 需求文档 / 提名轨迹 —— 各部门一致</text>
  <text x="340" y="202" text-anchor="middle" font-size="12" font-weight="500" fill="#444441">部门级领域专家 = 领域专家模板（平台）+ 部门知识包（部门）</text>
  <rect x="130" y="214" width="190" height="44" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="225" y="236" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#444441">领域专家模板（平台提供）</text>
  <text x="340" y="236" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#444441">+</text>
  <rect x="360" y="214" width="190" height="44" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="455" y="236" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#444441">部门知识包（部门文档 → RAG）</text>
  <rect x="40" y="278" width="600" height="200" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="60" y="302" font-size="13" font-weight="500" fill="#085041">部门级领域专家 —— 各部门提供，部门专属</text>
  <rect x="60" y="318" width="135" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="127" y="340" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">财务专家</text>
  <rect x="210" y="318" width="135" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="277" y="340" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">HR 专家</text>
  <rect x="360" y="318" width="135" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="427" y="340" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">IT 专家</text>
  <rect x="510" y="318" width="135" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="577" y="340" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">法务专家</text>
  <text x="60" y="384" font-size="11" fill="#0F6E56">部门专属本体：部门术语 / 领域规则（RAG 来源，各部门互不可见）</text>
  <rect x="60" y="396" width="560" height="64" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="80" y="418" font-size="12" font-weight="500" fill="#085041">部门的交付物 = 领域专家</text>
  <text x="80" y="440" font-size="11" fill="#5F5E5A">部门只交「部门文档 + 知识包」，平台用模板克隆生成领域专家；部门无需懂六元语。</text>
</svg>

---

## 7. 领域专家模板：六元语分工

- **模板写死**（平台维护，部门不可改）：identity 使命骨架 + reflection 反思机制 + guardians 三关校验
- **部门知识包填**（部门维护）：ontology 作业世界 + interface 对外暴露 + actions 会做什么

部门知识包的本体**允许引用平台通用本体**（如财务专家 ontology 里引用「需求条目」），但**禁止改写**（对齐本体单一事实源）。

<svg viewBox="0 0 680 430" width="100%" xmlns="http://www.w3.org/2000/svg">
  <title>领域专家模板：六元语分工</title>
  <desc>identity 使命骨架、reflection 机制、guardians 三关由模板写死，ontology、interface、actions 由部门知识包填写，生成后都要用户审批、测试、迭代进化。</desc>
  <text x="40" y="30" font-size="14" font-weight="500" fill="#444441">领域专家模板：六元语分工（写死 vs 部门填）</text>
  <rect x="40" y="46" width="600" height="134" rx="12" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="60" y="70" font-size="13" font-weight="500" fill="#3C3489">模板写死 —— 平台维护，部门不可改</text>
  <rect x="60" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="148" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">identity 使命骨架</text>
  <rect x="252" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="340" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">reflection 反思机制</text>
  <rect x="444" y="86" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="532" y="108" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#3C3489">guardians 三关校验</text>
  <text x="60" y="154" font-size="11" fill="#534AB7">身份骨架 + 怎么反思 + 三关护栏，全部门一致，防止部门改动破坏安全边界</text>
  <rect x="40" y="196" width="600" height="134" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="60" y="220" font-size="13" font-weight="500" fill="#085041">部门知识包填 —— 部门维护，模板只留空位</text>
  <rect x="60" y="236" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="148" y="258" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">ontology 作业世界</text>
  <rect x="252" y="236" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="340" y="258" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">interface 对外暴露</text>
  <rect x="444" y="236" width="176" height="44" rx="8" fill="#FFFFFF" stroke="#1D9E75" stroke-width="0.5"/>
  <text x="532" y="258" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">actions 会做什么</text>
  <text x="60" y="304" font-size="11" fill="#0F6E56">部门术语 / 规则 / 能力，来自部门文档 → RAG；可引用平台通用本体（如需求条目），但禁止改写</text>
  <rect x="40" y="346" width="600" height="64" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="60" y="368" font-size="12" font-weight="500" fill="#444441">共同规则：写完都要迭代 · 生成后用户审批 · 跑测试 · 测试中迭代进化</text>
  <text x="60" y="388" font-size="11" fill="#5F5E5A">两类六元语（写死的 + 部门填的）都不是一次定稿，都进同一个审批-测试-迭代闭环</text>
</svg>

---

## 8. 生成 → 审批 → 测试 → 迭代进化闭环

数字人（尤其是部门领域专家）不是一次生成即定型，走完整闭环：**生成 → 用户审批 → 跑测试 → 测试中迭代进化 → 注册上线**。部门反思若发现可能通用化的优化，可**升级提案 → 平台级审批 → 用户终审**，通过才回流平台通用方法论，否则留在部门层。

<svg viewBox="0 0 680 440" width="100%" xmlns="http://www.w3.org/2000/svg">
  <title>数字人生成到进化的审批测试迭代闭环</title>
  <desc>数字人生成后经用户审批、跑测试、测试中迭代进化，通过后注册上线；部门反思若通用化可升级提案到平台层经用户审批。</desc>
  <defs>
    <marker id="arrowL2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <text x="40" y="30" font-size="14" font-weight="500" fill="#444441">数字人：生成 → 用户审批 → 跑测试 → 迭代进化</text>
  <rect x="210" y="46" width="260" height="52" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="68" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#0C447C">生成数字人</text>
  <text x="340" y="86" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">模板写死 + 部门知识包合成</text>
  <line x1="340" y1="98" x2="340" y2="122" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="210" y="122" width="260" height="52" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="340" y="144" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#633806">用户审批</text>
  <text x="340" y="162" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">六元语逐段确认，不通过打回</text>
  <line x1="340" y1="174" x2="340" y2="198" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="210" y="198" width="260" height="52" rx="8" fill="#EAF3DE" stroke="#3B6D11" stroke-width="0.5"/>
  <text x="340" y="220" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#27500A">跑测试</text>
  <text x="340" y="238" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#3B6D11">用测试 case 验证提名-裁决全链路</text>
  <line x1="340" y1="250" x2="340" y2="274" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="210" y="274" width="260" height="52" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="296" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#3C3489">测试中迭代进化</text>
  <text x="340" y="314" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">发现问题 → 反思 → 改六元语 → 重新审批</text>
  <path d="M470 300 C 570 300, 570 148, 472 148" fill="none" stroke="#534AB7" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#arrowL2)"/>
  <text x="580" y="224" text-anchor="middle" font-size="11" fill="#534AB7">迭代回流</text>
  <line x1="340" y1="326" x2="340" y2="350" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="210" y="350" width="260" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="372" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#085041">注册上线</text>
  <text x="340" y="390" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">版本化注册 · 灰度切换 · 可回滚</text>
  <rect x="40" y="46" width="150" height="356" rx="12" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="60" y="70" font-size="12" font-weight="500" fill="#3C3489">反思升级提案</text>
  <text x="60" y="96" font-size="11" fill="#5F5E5A">部门反思发现某优化</text>
  <text x="60" y="114" font-size="11" fill="#5F5E5A">可能通用化时：</text>
  <rect x="56" y="130" width="118" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="115" y="152" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#3C3489">提出升级提案</text>
  <line x1="115" y1="174" x2="115" y2="196" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="56" y="196" width="118" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="115" y="218" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#3C3489">平台级审批</text>
  <line x1="115" y1="240" x2="115" y2="262" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowL2)"/>
  <rect x="56" y="262" width="118" height="44" rx="8" fill="#FFFFFF" stroke="#7F77DD" stroke-width="0.5"/>
  <text x="115" y="284" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#3C3489">用户终审</text>
  <text x="60" y="330" font-size="11" fill="#5F5E5A">通过 → 回流平台</text>
  <text x="60" y="348" font-size="11" fill="#5F5E5A">通用方法论</text>
  <text x="60" y="366" font-size="11" fill="#5F5E5A">拒绝 → 留在部门层</text>
  <text x="60" y="384" font-size="11" fill="#5F5E5A">不污染其他部门</text>
</svg>

---

## 9. 数字人创建：部门知识包流水线

部门文档进来后分流两路：**RAG** 搭建数字人的先验知识 + 边界（可替换、持续增长）；**本体分解器**从文档中归纳部门专属本体（只产候选提名，过三关校验 + 分级审批才入库）。两路合成部门知识包，与写死的领域专家模板合成数字人，进入第 8 节闭环。

<svg viewBox="0 0 680 890" width="100%" xmlns="http://www.w3.org/2000/svg">
  <title>数字人创建流水线：RAG + 本体分解器到部门知识包再到合成闭环</title>
  <desc>部门文档分流到 RAG 知识层和本体分解器；本体分解器经抽取、定义、规范化产出候选本体提名，过三关校验与分级审批成为部门专属本体；与 RAG 知识库合成部门知识包，再与写死的领域专家模板合成数字人，进入审批测试迭代闭环。</desc>
  <defs>
    <marker id="arrowP" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>
  <text x="40" y="28" font-size="14" font-weight="500" fill="#444441">数字人创建：部门文档 → RAG + 本体分解器 → 部门知识包 → 合成 → 闭环</text>
  <rect x="240" y="44" width="200" height="44" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="340" y="66" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#444441">部门文档</text>
  <text x="340" y="84" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#5F5E5A">制度 · 流程 · 术语 · 规则</text>
  <line x1="340" y1="88" x2="185" y2="112" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <line x1="340" y1="88" x2="495" y2="112" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="40" y="112" width="280" height="170" rx="10" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="180" y="134" text-anchor="middle" font-size="13" font-weight="500" fill="#085041">RAG · 先验知识 + 边界</text>
  <text x="60" y="158" font-size="11" fill="#085041">① 分段（语义 / 结构）</text>
  <text x="60" y="178" font-size="11" fill="#085041">② 段级 summary + 多标签</text>
  <text x="60" y="198" font-size="11" fill="#085041">③ 聚合整篇 summary</text>
  <text x="60" y="218" font-size="11" fill="#085041">④ 对 summary 做 embedding（检索入口）</text>
  <text x="60" y="238" font-size="11" fill="#085041">⑤ 命中 summary → 回取原文</text>
  <text x="180" y="266" text-anchor="middle" font-size="11" fill="#0F6E56">可替换 · 任务中持续增长</text>
  <rect x="360" y="112" width="280" height="170" rx="10" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="500" y="134" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">本体分解器（混合式归纳）</text>
  <text x="380" y="158" font-size="11" fill="#0C447C">① 抽取：EDC 开放式三元组（无 schema）</text>
  <text x="380" y="178" font-size="11" fill="#0C447C">② 定义：实体 / 关系自然语言定义</text>
  <text x="380" y="198" font-size="11" fill="#0C447C">③ 规范化：定义向量聚簇 + LLM 复核</text>
  <text x="380" y="218" font-size="11" fill="#0C447C">　　对齐平台通用本体（只引用 · 禁改写）</text>
  <text x="500" y="266" text-anchor="middle" font-size="11" fill="#185FA5">DeepSeek 抽取提名 · GLM 跨文档归纳核验</text>
  <line x1="180" y1="282" x2="180" y2="314" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <line x1="500" y1="282" x2="500" y2="314" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="40" y="314" width="280" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="180" y="334" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">RAG 知识库</text>
  <text x="180" y="352" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">可替换 · 持续增长</text>
  <rect x="360" y="314" width="280" height="52" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="500" y="334" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">候选本体提名</text>
  <text x="500" y="352" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">只提名 · 无终审权</text>
  <line x1="500" y1="366" x2="500" y2="392" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="360" y="392" width="280" height="76" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="500" y="412" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#633806">三关校验（确定性代码裁决）</text>
  <text x="500" y="432" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">结构 · 语义 · 证据（span 逐字回原文）</text>
  <text x="500" y="452" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">不过 → 修复 ≤2 轮 → 转 unknown</text>
  <line x1="500" y1="468" x2="500" y2="494" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="360" y="494" width="280" height="52" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="500" y="514" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#633806">分级审批</text>
  <text x="500" y="532" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">低风险自动 · 高风险人工终审</text>
  <line x1="500" y1="546" x2="500" y2="572" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="360" y="572" width="280" height="52" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="500" y="592" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">部门专属本体</text>
  <text x="500" y="610" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">可引用平台通用本体 · 禁止改写</text>
  <line x1="180" y1="366" x2="240" y2="640" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <line x1="500" y1="624" x2="440" y2="640" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="140" y="640" width="400" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="660" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#085041">部门知识包</text>
  <text x="340" y="680" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#0F6E56">ontology + interface + actions</text>
  <line x1="340" y1="696" x2="340" y2="722" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="100" y="722" width="480" height="60" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="340" y="742" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">合成生成数字人</text>
  <text x="340" y="764" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">领域专家模板（identity + reflection + guardians 写死）+ 部门知识包</text>
  <line x1="340" y1="782" x2="340" y2="806" stroke="#888780" stroke-width="1.5" marker-end="url(#arrowP)"/>
  <rect x="100" y="806" width="480" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="826" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#3C3489">用户审批 → 跑测试 → 测试中迭代进化 → 注册上线（第 8 节闭环）</text>
  <text x="340" y="846" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">反思：知识缺口反哺 RAG · 通用化优化走升级提案</text>
</svg>

### 9.1 RAG 流水线（已定）

分段 → 段级 summary + 多标签 → 聚合整篇 summary → 对 summary 做 embedding（检索入口）→ 查询命中 summary 回取原文。定位：持续可替代、任务中可不停增加的内容；创建数字人时注入先验知识与边界。

### 9.2 本体分解器（调研收敛 · 方案待确认）

定位：从部门文档**抽取归纳**本体（「归纳器/抽取器」语义，非「切分器」）。业界两条正交路线：

- **自底向上 · 无模式（EDC，Extract-Define-Canonicalize）**：先开放式抽取三元组（不喂 schema、宁多勿漏），再为每个实体/关系生成**自然语言定义**，最后用「定义向量相似 + LLM 复核」聚簇规范化。无预定义 schema 也能收敛出干净本体。
- **自顶向下 · 本体约束（GraphRAG 系）**：把本体 Schema 注入 prompt 引导抽取 SPO 三元组，再用类型约束 Φ(r) = (dom, range) 事后校验。降噪、可对齐平台本体，但约束过强图会稀疏。

拟采用**混合式**（与提名-评审分离同构，LLM 全程只提名、无终审权）：

1. **抽取**（EDC 开放式，DeepSeek V4 Flash 高频调用）：不喂 schema，自由抽实体/关系/属性三元组——产出只是候选提名。
2. **定义**：为每个候选实体/关系生成自然语言定义（LLM 靠定义对齐远比靠标签对齐准确）。
3. **规范化**（GLM 5.2 跨文档归纳核验）：定义 embedding 召回近邻 → LLM 验证是否合并；对齐平台通用本体（**只引用、禁改写**）；实体消解（同义异名合并）；低频候选过滤。
4. 产出**候选本体提名** → 既有三关校验（结构/语义/证据 span 逐字回原文）+ 分级审批 → 才进部门本体库。

### 9.3 反思触发条件（待定义）

数字人单独迭代、非每件事后都迭代，具体触发阈值待定义。

---

*生成说明：本版为 v5，在 v4「数字人分类 + 领域专家模板 + 审批测试迭代闭环」基础上新增第 9 节数字人创建流水线（RAG 已定 + 本体分解器调研收敛方案 + 部门知识包合成）。配色语义统一（蓝=主流程、绿=放行/命中、紫=反思/迭代、橙=判定/约束/审批、青=领域知识、灰=通路库）。*
