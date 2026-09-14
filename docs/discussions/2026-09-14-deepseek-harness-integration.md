# DeepSeek Harness 调研 + 与 digitalManCreateHub 集成方案

> 日期：2026-09-14 · 状态：**调研结论 + 集成选型（待用户拍板）**
> 上游依据：GitHub `deepseek-ai/deepseek-harness`（master 分支，仓库版本 0.1.5-rc）· 官方文档站 `deepseek-harness.github.io` · MIT 许可
> 本地副本：`E:\electronicEmployee\deepseek-harness`（浅克隆，track 最新 master）
> 本文只做**结论 + 方案选项**，不含代码改动。方向确认后再进入实现。

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| DSH 是什么 | DeepSeek 官方开源的 **Agent 运行时框架**（不是模型、不是聊天客户端）。CLI 名 `dsh`，定位 "Model + Harness = Agent"。2026-08-13 开源，MIT，TypeScript/Node.js |
| 核心特征 | **一切皆插件**（基于 Cordis 元框架）：模型适配器、工具注册表、会话日志、**乃至 agent loop 本身**都是可替换插件，"没有需要打补丁的特权内核" |
| 能不能和你项目结合 | **能，而且接缝非常干净**——两者在最核心的三处（LLM 双通道 / 执行闸门 / 工具与 MCP）几乎是同一套抽象，DSH 相当于把你手写的那部分做成了标准化插件床 |
| 最关键的一个发现 | **DSH 的裁决模型与你的铁律 L1 是同构的**（读源码确认，见 §3.2）：`ctx.tools.guard()` 只能 deny/abstain、**永不 force-allow**；`ctx.approval` 审批接缝要求 `allowed-once` 才放行、**无 answerer 即 fail-closed**、**刻意不提供 `allow_always`**。这三条正好是"LLM 无终审权 + 用户是唯一终审点 + 判别链不静默兜底"的代码级形态 |
| 结合在哪里 | **3 个明确接缝**（LLM provider ⇄ `llm.py`；执行闸门 ⇄ 三关校验 + `guard_action`；工具表 ⇄ `actions.py` + `mcp.py`），外加 1 个高价值用途（评测层 ⇄ `benchmark.py`） |
| 推荐形态 | **A 起步 + B 进阶**：先用 DSH 当**评测/复现运行时**（低风险、不动主链），验证通过后再把它作为**执行类动作的运行时**（收益最大）。**不建议全量接管主链** |
| 主要风险 | ① **L1 铁律**：DSH 的 agent loop 默认让模型自由决策，虽有现成机制可用，但**必须主动接线**，接线前不可放任（全部集成工作的第一优先级）；② 开发者预览、官方声明会破坏性变更 → 须 pin 版本；③ `sdk-minimal` 固定 `danger-full-access`；④ DSH 的 Windows 沙箱是 ACL 受限令牌而非容器隔离；⑤ 官方当前不接受外部 PR |

---

## 1. DeepSeek Harness 是什么

### 1.1 定位

它是**模型与环境的中间层**：负责把模型接到文件系统、终端、网页、代码工具和其他 Agent 上，并组织上下文、工具调用与任务执行。官方公式：

```
Model + Harness = Agent
```

背景：2026-05 内部立项（对标 Claude Code），2026-07-31 首次以"极简模式"参与 DeepSeek V4-Flash 的公开 Code Agent 评测，2026-08-13 v0.1 开发者预览版开源。发布后星数极速增长（8 月中 ~9 万 → 9 月初 ~21 万）。

### 1.2 基本盘

| 项 | 值 |
|---|---|
| 仓库 | `github.com/deepseek-ai/deepseek-harness` |
| CLI | `dsh` |
| 许可 | MIT（可商用） |
| 语言/栈 | TypeScript + Node.js（要求 Node 22.19+ / 24+），pnpm monorepo（pin `pnpm@11.7.0`） |
| 版本 | v0.1 开发者预览；master 已到 `0.1.5-rc`。**官方明确声明会有破坏性变更** |
| 依赖底座 | Cordis（vendored 4.0.2），论文 arXiv:2608.25512 |
| 快速启动 | `npx @deepseek-ai/dsh web` → `http://127.0.0.1:3080` |
| 源码运行 | `git clone && pnpm install && pnpm run build && pnpm dsh web` |
| 贡献 | 当前**不接受外部 PR**，只收 GitHub Discussions 反馈 |

### 1.3 「一切皆插件」到底是什么

Cordis 是**元框架**，只管两件事：插件的加载/卸载 + 依赖关系。DSH 的所有具体组件都是 Cordis 插件：

- 模型适配器、工具注册表、会话日志、system prompt 组装、沙箱、存储、调度、UI、**agent loop 自身**
- 插件向共享 context 贡献三类东西：**服务（services）**、**类型化事件（typed events）**、**可逆效果（reversible effects）**
- 注册是"效果"——插件卸载时自动回滚，不留孤儿状态
- 扩展方式 = **在旁边挂一个新插件**，而不是 fork / patch 核心

### 1.4 四种运行模式

| 模式 | 加载内容 | 适用场景 |
|---|---|---|
| **标准模式** | 完整工具集（文件系统、shell、web 搜索、subagent、plan 模式） | 日常 Agent 工作 |
| **PTC / Code 模式** | 程序化工具调用：不暴露单个 function call，而是生成 TypeScript SDK 让模型**写一段代码**编排多轮调用 | 复杂多步工具流（把 5 次往返压成 1 次） |
| **极简模式**（minimal） | 只保留 shell + `str_replace_editor` | **模型基准测试**（DSH 官方就是这样评测自家模型的） |
| **创造模式**（creator） | 标准模式全部 + 运行时检查 + 内存内插件试验 + 预设编写指引 | 构建自定义插件组合 |

> 对你最直接相关的是 **极简模式**——你 `benchmark.py` 里 G0/G1/G2/G3 四组对照的"裸模型/G1/G2"三组，本来就想要一个最小、可复现的执行环境；DSH 极简模式正是为此设计的。

### 1.5 追记式会话日志 + Trajectory 视图

这是 DSH 最对你胃口的设计：

- **任何抵达模型请求的内容都必须能从日志重建**（运行时不变式强断言）——system prompt、思维链、工具调用与结果、subagent 调度、**每一次上下文注入**全部落进同一条 append-only 事件流
- 恢复 / 分叉 / 回放 / transcript / 遥测 / Web UI **共享同一份事件流**
- **Model-visible means logged**：想新增一种"模型可见输入"，就必须新增一种 session event
- 会话格式带版本与相邻迁移包（`vN -> vN+1`），已提交的代路径永不重命名/替换/删除

> 这与你的 **L3「绝对准确 / 证据可引」** + `jobs/events` 事件流 + checkpoint 断点续跑是同构的。差别只在于：DSH 把它做成了**强制不变量**，你是靠约定 + 自建表。

### 1.6 沙箱与权限

- 本地后端把子进程包在：**Linux Landlock**（DeepSeek 自写 Node addon）/ **macOS Seatbelt** / **Windows ACL restricted-token runner**
- `dsh-base` 层内含 sandbox 与审批策略；`ctx.sandbox` 是接缝（consumer 在 spawn 前包裹 argv）
- **执行世界共享**：文件系统与子进程 provider 指向远程 sandbox 时，Bash / PTY / LSP 会**一起迁移**
- 权限分层从"只读"到"工作区受限"再到"完全不受限"。**注意**：`sdk-minimal` profile 固定 `danger-full-access`

> ⚠️ 你在 **Windows 原生**跑，DSH 的 Windows 分支是 ACL restricted-token，跟你现有的 Docker 沙箱（`mcp.py`，per-user 容器 + docker.sock）不是一回事。两者可以共存，但**不要以为 DSH 的沙箱能替代你的 Docker 隔离**。

### 1.7 模型无关 + 互操作

- 内置 provider 目录：DeepSeek、Anthropic、OpenAI、AWS Bedrock、Azure、Gemini，**`moonshotai`（Kimi）、`zai`（GLM）**
- 自定义 provider：任意 OpenAI 兼容网关，`api` 三选一 → `openai-completions` / `openai-responses` / `anthropic-messages`
- `compat.thinkingFormat: deepseek` 等兼容开关解决网关差异（`role: developer`、`max_completion_tokens` 等）
- 自带 **MCP client**、**ACP（Agent Client Protocol）** 支持、可读 `AGENTS.md` / `CLAUDE.md`
- 可选 subagent provider：直接把活派给 **Claude Code** 或 **Codex**（默认关闭，从 PATH 解析二进制，登录自理）

---

## 2. 架构解剖（集成必须懂的四个概念）

### 2.1 Profile / Bundle / 层序

| 概念 | 说明 |
|---|---|
| **Profile** | 存在 Harness home 里的**命名组合**。记录它堆叠的 bundle、安装的树外插件、用户自己的 `cordis.patch.yml`。自带模板：`web` / `headless` / `sdk` / `sdk-minimal` / `acp` |
| **Bundle** | Cordis 配置行 + 挂载代码的**分发格式**。在自身 `package.json` 的 `dsh` 字段声明（`dsh.profile` / `dsh.bundle`）。插入的任何东西都**保持可被上层 patch** |

层序（依次叠加到空条目列表）：`profile 内 bundle 顺序` → `profile 的 cordis.patch.yml` → `home 级 patch` → `--patch overlay`。
查看实际启动的树：`dsh --profile web --dump-config`（输出任意一行都可被自己的 patch 替换）。

### 2.2 核心包与它们的 ctx key

| 包 | 职责 | ctx key |
|---|---|---|
| `core/session` | 追记式 `SessionEvent` 日志与内存存储 | `ctx.sessions` |
| `core/system-prompt` | prompt 分段 + 工具 schema 组装 | `ctx.systemPrompt` |
| `core/tools` | **作用域化工具注册表 + 受保护执行管线** | `ctx.tools` |
| `core/agent` | `Agent` 接口、实时注册表、`agent/*` 事件 | `ctx.agents` |
| `core/agent-loop` | 默认驱动器（可替换！） | `ctx.agentLoop` |
| `core/scope` | 按 agent 作用域注册的原语 | 库，无 key |
| `llm/llm` | 消息/流词汇表 + 适配器接缝 | `ctx.llm` |
| `webhook/webhook` | 认证投递分发 + Workspace Session 创建 | `ctx.webhookRuntime` |

### 2.3 扩展接缝（seam）—— 三个角色

一个 **seam** = **Service Definition（声明接口）+ Service Provider（实现）+ Consumer（使用，通常是面向模型的工具）**。
一个包可合并多个角色，但单独一个角色不构成接缝。**接缝的价值：换一个 provider 就能改变整个产品。**

### 2.4 事件三域 & "新行为放哪里"

| 事件域 | 性质 | 用途 |
|---|---|---|
| **Session events** | 持久事实，追加到日志，`session/event` 广播 | 需跨重载存活的 |
| **Agent events**（`agent/*`） | 携带实时 `Agent`（inbox/step/status/request/validation/continuation） | 观察或拦截**进行中**的工作 |
| **Capability events** | 向接缝（`fs/*`、`tools/*`、`telemetry/*`）附加策略与适配器 | **不需要 import loop 就能加策略** |

工具执行管线的关键事件序列：

```
tool/call* → tools/pre-execute → tools/execute → tools/post-execute → tool/result*
                ↑ waterfall（监听器必须调用 next() 才能委托下去）
```

其他关键映射（官方表，节选与你相关的）：

| 目标 | 机制 |
|---|---|
| 添加模型 provider | 在 `ctx.llm` 注册适配器 |
| 添加面向模型的能力 | 在 `ctx.tools` 注册；schema 自动进 prompt 组装 |
| 添加人类命令（不经模型 turn） | 在 `ctx.commands` 注册 |
| 拦截请求 / 工具 / turn | 监听对应 `agent/*` 或 `tools/*` 事件 |
| 添加面向模型的上下文 | 调用 `agent.inject()`（落入下一个被接纳的请求） |
| 添加持久会话状态 | 扩展 `SessionEventMap`，从日志渲染与重放 |
| 换会话存储后端 | 实现 `SessionPersistence`（create/open/stat/list/export） |
| 限制派生进程 | `ctx.sandbox` 后端；consumer 在 spawn 前包裹 argv |

### 2.5 Python SDK（对你的后端最关键的入口）

| 项 | 值 |
|---|---|
| 安装 | `python -m pip install deepseek-harness-sdk`（自动装同版本 runtime wheel，**不需要系统 Node**） |
| 模块 | `deepseek_harness`（高层 turns API + 低层 JSON-RPC client）；`deepseek_harness_runtime`（bundled `dsh` CLI） |
| 通信 | 以子进程方式启动 `dsh --profile sdk`，走 **stdio 上的 newline-delimited JSON-RPC** |
| 前提 | Python 3.10+；**每次启动必须显式指定 Harness home**（SDK 永不偷偷读 `~/.dsh`） |
| 平台 | Linux x64/arm64、macOS 14+ arm64、**Windows x64** |

最小用法：

```python
from deepseek_harness import DeepSeekHarness

with DeepSeekHarness(
    dsh_home="/absolute/path/to/isolated-dsh-home",
    cwd="/absolute/path/to/workspace",
    provider="deepseek-official",
    model="deepseek-v4-flash",
    reasoning_effort="max",
    max_tokens=49_152,
) as harness:
    result = harness.run("Say hi.", session_id="example-001")

print(result.final_response)   # RunResult(session_id, final_response, finish_reason, events, notifications)
```

`provider` / `model` / `reasoning_effort` / `max_tokens` 在 JSON-RPC 初始化时下发；`base_url` / `api_key` 可显式覆盖 `DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY`。
自定义插件持久化：`dsh plugin --profile sdk add file:/path/to/my-plugin-bundle`（这条才需要 pnpm）。
单次改动：传 `patches=(".../x.patch.yml",)`。

---

## 3. 与你项目的结合点（逐条映射）

你项目的抽象 | 它在哪 | DSH 里对应什么 | 结合方式
---|---|---|---
**LLM 双通道**（V4-Flash 提名 / GLM 判别） | `backend/app/llm.py` | `ctx.llm` 适配器 + provider 目录（`deepseek-official`、`zai`）+ 自定义 OpenAI 兼容网关 | **零改造**：你的两个端点本来都是 OpenAI 兼容。GLM 直接选 `zai`；V4-Flash 选 `deepseek-official`；Ollama 走自定义 provider
**三关校验 + 执行前把关** | `benchmark.py` / `actions.py` 的 `guard_action` | `tools/pre-execute` **waterfall 事件** | **这是全项目最重要的一处**：把三关校验实现成 pre-execute 监听器，LLM 提名 → 你裁决 → `next()` 放行 or 中断
**数字人动作表（builtin + mcp）** | `backend/app/actions.py`（`ontology_retrieve` / `rag_retrieve` / `run_code` / `run_test` / `read_file` / `write_file`） | `ctx.tools` 作用域化工具注册表 | 每个 action 包成一个 DSH 工具插件；`input_schema` 已是 JSON Schema，**可以直接搬**
**MCP 沙箱** | `backend/app/mcp.py`（Docker per-user 容器 + docker.sock） | DSH 自带 **MCP client** | DSH 只当**客户端**连你的 MCP server；你的 Docker 隔离层保持不动
**执行类动作的反应式循环** | `actions.py` 的 exec 类动作（run_code/run_test） | `core/agent-loop`（**可替换**）+ 标准模式 | 用 DSH 标准模式的 loop 替掉你手写的循环；行动闭环（写→测→改）由 DSH 的 plan + tools 承担
**四组对照评测** | `backend/app/benchmark.py`（G0/G1/G2/G3 + GLM 判分 + 版本快照回滚） | **极简模式** + 追记式日志 + `headless`/`sdk` profile | **A 方案主战场**：用极简模式跑 G0，用 headless 跑 G1/G2/G3；日志天然可复现、可回放
**jobs / events 事件流** | `backend/app/jobs.py` + `db` | `SessionEvent` 追记日志 + `agent/*` 事件 | 你的 job 事件流可**订阅** DSH 的 agent 事件，把 DSH 执行过程记进你自己的 jobs 表
**pipeline 编排** | `pipeline.py` / `pipeline_factory.py` | `Agent Teams` 包 + subagent 机制 | 远期：把 pipeline 节点映射成 subagent 调度
**本体单一事实源 / 六元组** | `schema.sql` + `ontology.py` + `identity.py` | **无对应物** | ⚠️ DSH 不关心你的本体。你的本体仍然是你自己的真相源，DSH 只提供执行与记录
**前端 8 页 + 深色主题** | `client/` | `dsh web` 是**独立的** Web UI | ⚠️ 不要合并：SDK 与 web 是两条 CLI 应用，web 不能服务 SDK 客户端。你的前端继续自建，DSH 只做后端运行时

### 3.1 一句话总结结合逻辑

> **你缺的不是"再写一个 agent loop"，而是把 loop 标准化、可复现、可审计。**
> DSH 在 LLM 适配、工具注册、执行闸门、追记日志这四处，和你已经手写的东西**抽象层级完全一致**，但它把它们做成了有版本、有接缝、有生态的插件床。你的**本体/六元组/三关/评级体系**恰好是 DSH 没有的——两边是互补，不是替代。

### 3.2 重点：DSH 的裁决模型与你铁律 L1 是**同构的**（读源码后的结论）

这是整个调研里最有价值的发现。DSH 内置了两个恰好为你 L1 准备的机制，**语义与你的设计语言几乎一一对应**：

**(a) `ctx.tools.guard()` —— 单调守卫，只能拒绝或弃权，永不强制放行**

```ts
type ToolGuard = (execution: Readonly<ToolExecution>) => string | undefined
// 返回理由 = 拒绝；返回 undefined = 保持原状。
// 设计意图（原文）："guards have no allow result, so listener ordering
// cannot turn a denial back into permission."
```

> 翻译成你的话：**这条守卫永远不可能把 LLM 的提议"洗白"成允许**。这正是"LLM 无终审权"的代码级落地形态——**LLM 的提名走 waterfall（可 allow/deny/ask），你的确定性不变量走 guard（只能收窄，永不放大）**。你现在的 `guard_action` 想表达的语义，DSH 用了更严格的类型来强制。

**(b) `ctx.approval` 审批接缝 —— 一次一授权 + 失败即拒绝 + 审计配对**

| DSH 的机制 | 语义 | 你的对应设计 |
|---|---|---|
| `PreToolDecision = allow \| deny \| ask` | 执行前三选一 | 三关校验后 → 放行 / 驳回 / 提审 |
| `ask` → 交给 `ctx.approval` | **只有 `allowed-once` 才放行** | **用户是唯一终审点** |
| 无 answerer / 通道缺失 / agent-less → `unavailable` → **拒绝** | **fail-closed 是零配置默认，不是配置项** | 判别链失败**不静默兜底**（异源交叉核验） |
| **只提供一次性授权，刻意不提供 `allow_always`** | 授权不跨调用持久化 | 你的"pending change set + 用户逐条审批 + 版本快照回滚" |
| `approval/asked` / `approval/decided` 审计对，**log-only、模型不可见** | 审计与呈现分离 | 你的 `jobs/events` + `persona_ontology_changes` |
| `policy: 'never'` 在**服务内部**直接判 `rejected`，任何注册顺序都绕不过 | 不可旁路 | 「代码定路径」 |
| 子 agent 的审批被钉死为 `never` | 委派 ≠ 放权 | 提名-裁决分离 |

> 结论：**你不需要为了集成 DSH 而放弃 L1，反而可以把 L1 表达得更严格**——用 `guard` 承载"永不放大"的硬不变量，用 `ctx.approval` 承载"用户终审"，用 `tools/pre-execute` 承载三关校验的提名过滤。三者职责天然分离。

**(c) 工具执行管线的完整相位**

```
tools/pre-execute   allow / deny / ask          ← 三关校验 + 提名
        ↓
ctx.tools.guard()   只有 deny / abstain          ← 硬不变量（永不放大）
        ↓
tools/execute       包装器（超时/重试/度量）      ← 你的 jobs 超时与重试
        ↓
tools/post-execute  accept / replace / block     ← 确定性校验 + 纠正反馈
        ↓
finalizeContent     工具自带的内容最后一道不变量  ← 证据 span 校验？
        ↓
tools/result        只读不可变快照，观察者失败被隔离 ← 审计落库
```

**(d) 权限预设与沙箱层级（修正第 1.6 节的模糊表述）**

| 预设 | 内容 | 含义 |
|---|---|---|
| `workspace-write` | 工作区可写 + 审批策略 `ask` | **默认预设** |
| `danger-full-access` | 完全放开 + 审批策略 `never` | 危险档（`sdk-minimal` 固定用这个） |

- 文件沙箱**起始模式默认 `read-only`**（fail-safe 默认值，源码注释：`mode: 'read-only' is the fail-safe default`）
- 一次预设切换 = 写一个 `permission/preset` 事件，**同时贯通到"沙箱模式"和"审批策略"两个旋钮**（由 `ctx.permissionPresets` 拥有）
- 沙箱包结构：`sandbox`（词汇/升级/roots）+ `sandbox-local`（landlock / bwrap / seatbelt / **windows-acl**）+ `sandbox-policy` + `sandbox-windows-acl`
- **沙箱升级**走的是审批接缝的同一个 `ask` 通道（`escalation-approved` / `escalation-rejected` 两个已录制的快照场景）

---

## 3.3 其他值得注意的现成能力

| 包 | 做什么 | 你可能用在哪 |
|---|---|---|
| `packages/guard/repeat-tool-reminder` | 模型重复同一工具调用时提醒它换方法 | 你的反应式循环防死循环 |
| `packages/guard/timeout-policy` | 声明了超时的工具调用按期超时并返回明确错误 | 你的 `jobs` 超时 |
| `packages/skill`（`skill` / `skill-filesystem` / `tool-skill` / `skill-badge`） | 技能加载与面向模型的技能工具 | 你的"部门知识包"可作为 skill 分发 |
| `packages/goal`（`goal` / `goal-round-driver` / `tool-goal`） | 目标与轮次驱动 | 你的"训练 auto-iterate"循环 |
| `packages/plan/plan-mode` | 计划模式 | 数字人的任务分解（Pipeline 前置） |
| `packages/subagent/*` | in-process / fork / ACP / Claude Code / Codex / DSH-SDK 多种 subagent | 你的 pipeline 节点调度 |
| `packages/hooks`（`hook-protocol` / `hooks-claude-code` / `hooks-codex`） | **可复用你已有的 `hooks.json`**，hook 能阻断 prompt 或工具调用 | 若你已有 hook 配置可直接搬 |
| `packages/mcp` | MCP 客户端 | 对接你的 `mcp.py` 沙箱 |
| `packages/session-query` | 会话检索 | 你的 RAG 会话检索可复用其投影 |
| `packages/spill` | 超大工具结果截断 + 落盘定位 | 你的 `ANALYSIS_MAX_ENTRIES` 类似约束 |

---

## 4. 三种结合形态（选型对比）

| | **A. 评测层融合** | **B. 执行层融合** | **C. 全量接管主链** |
|---|---|---|---|
| 做什么 | DSH 只跑 benchmark 四组对照 + 复现/回放 | DSH 作为 `run_code`/`run_test`/读写文件等**执行类动作**的运行时 | 用 DSH 的 loop + 会话 + web UI 替掉你的 FastAPI 主链 |
| 改动面 | 新增 `backend/app/dsh_eval.py`，不动现有链路 | 新增 DSH 工具插件 + pre-execute 闸门；`actions.py` 里的 exec handler 改为转发 | 重构整个后端与前端 |
| 触及文件 | `benchmark.py`（旁路调用） | `actions.py`、新增 `dsh_plugins/` | 全部 |
| 风险 | **低** | **中**（需要把三关语义完整搬进钩子） | **高** |
| 收益 | 评测可复现、可回放、可换模型横向比 | 真正拿到 agent 能力（写代码闭环）+ 审计日志 | 架构统一 |
| 前置条件 | Python SDK 装通、Harness home 隔离 | A 已完成 + 插件开发（需 pnpm） | A+B 稳定运行数周 |
| **建议** | **现在做** | **A 验证后做** | **不做**（或只做远期架构探索） |

**推荐：A → B，跳过 C。** 理由：
1. 你的**主链价值在本体与裁决**，不在 loop。全量接管等于把 L1 铁律的落地点交给一个开发者预览版框架。
2. DSH 明确声明会有破坏性变更 → 只用在**可替换的边缘层**（评测、执行），主链保持自持。
3. A 方案的收益已经很大：benchmark 的"可复现性"是你现在最弱的一环（GLM 判分 + 本地 Ollama 四组对照，环境漂移难复现）。

---

## 5. 推荐落地路径（分阶段）

### 阶段 0：环境验证（不做任何集成）
- [ ] 在本机跑通 `npx @deepseek-ai/dsh web`，确认 Node 22.22.2 满足要求
- [ ] 建一个**隔离的** `DSH_HOME`（例：`E:\electronicEmployee\deepseek-harness\home-eval`），**绝不与项目数据混**
- [ ] 在 Settings → Models 里配三个 provider：`deepseek-official`、`zai`(GLM)、自定义 provider 指向本地 Ollama（`http://localhost:11434/v1`）
- [ ] 用极简模式 headless 跑一条，确认能起、能停、日志落在哪

### 阶段 1：A 方案——评测层融合
- [ ] 装 Python SDK：`python -m pip install deepseek-harness-sdk`
- [ ] 新增 `backend/app/dsh_eval.py`：用 `DeepSeekHarness` 驱动 G0 裸模型臂，与现有 G1/G2/G3 结果对齐到同一份 `benchmark` 指标
- [ ] 把 DSH 的 `RunResult.events` 落进你自己的 `jobs/events` 表（只增不改现有 schema）
- [ ] 消融对比：**同一批 quiz，DSH 极简模式 vs 你现有 Ollama 直调**，比准确率/幻觉率/拒答率是否一致（这本身就是一份好的 A/B 报告）

### 阶段 2：B 方案——执行层融合
- [ ] 新建 `dsh_plugins/`（项目级），把 `actions.py` 的 exec 类动作包成 DSH 工具插件
- [ ] 把**三关校验 + guard_action** 实现为 `tools/pre-execute` waterfall 监听器（**L1 铁律的落地点**）
- [ ] 补丁文件固定 profile 组合；`dsh --profile ... --dump-config` 纳入版本管理
- [ ] 权限层：**禁止使用 `danger-full-access`**，改用工作区受限层（与你的 Docker 沙箱形成双层）

---

## 6. 风险与铁律冲突清单（必须逐条过）

| # | 风险 / 冲突 | 影响 | 对策 |
|---|---|---|---|
| R1 | **DSH 的 agent loop 默认让模型自由决策**，与你 L1「LLM 无终审权 / 提名-裁决分离」直接冲突 | **最高**——若放任，等于把终审权交回 LLM | **已找到现成机制（见 §3.2）**：裁决不变量用 `ctx.tools.guard()`（只能 deny/abstain，永不 force-allow）；用户终审用 `ctx.approval`（`allowed-once` 才放行，无 answerer 即 fail-closed）；三关校验放 `tools/pre-execute`。agent loop 若仍不够，可整体替换（`ctx.agentLoop` 是接缝） |
| R2 | 开发者预览，**官方声明会有破坏性变更** | 升级即 break | 固定版本（pin）；只用在边缘层；升级前读 release notes |
| R3 | `sdk-minimal` profile 固定 `danger-full-access` | 逃逸风险 | 不要用 sdk-minimal 跑有写权限的任务；用 `sdk`/自定义 profile + `ctx.sandbox` |
| R4 | DSH Windows 沙箱 = ACL restricted-token，**非容器隔离** | 与你的 Docker per-user 隔离不等价 | 两者并存：DSH 在其内层限制，Docker 在外层兜底。不要用 DSH 沙箱**替代** Docker |
| R5 | 会话日志会记录**每一次上下文注入**（含你的本体片段） | 数据落盘/外泄面变大 | Harness home 独立、不入 git；敏感本体片段考虑走 `agent.inject()` 时打标或脱敏 |
| R6 | 官方**当前不接受外部 PR** | 你的定制无法上游化 | 一切定制走 `cordis.patch.yml` + 你自己的插件 bundle，不指望上游合并 |
| R7 | DSH 不关心本体/六元组 | 本体真相源不会自动同步 | 本体仍以你的 PG 为唯一事实源；DSH 侧只做只读引用 + 结果回写 |
| R8 | Cordis 插件模型学习曲线 | 上手成本 | 先读官方 `cordis-primer.md` + `cordis-tutorial/`（仓库自带中英对照） |

---

## 7. 保存位置与目录约定

| 内容 | 路径 |
|---|---|
| DSH 仓库副本 | `E:\electronicEmployee\deepseek-harness\`（浅克隆，track master） |
| 本项目讨论记录 | `E:\electronicEmployee\digitalManCreateHub\docs\discussions\` |
| DSH 隔离 home（建议） | `E:\electronicEmployee\deepseek-harness\home-eval\`（**不提交 git**） |
| DSH 关键文档（仓库内） | `docs/architecture.md`（架构）· **`docs/capability-seams.md`（全部接缝与 ctx key 总表，最有用的索引）** · `docs/cordis-primer.md`（Cordis 入门）· `docs/subsystems/`（tools / core / shell / jobs / credentials 等子系统）· `docs/user/guide/`（使用，含中英对照）· `docs/user/develop/`（插件开发）· `docs/config-catalog.md`（**全部插件配置字段与默认值，源码生成**）· `SAFETY.md` · `python/sdk/README.md` |
| 关键设计决策（Agent Notes） | `.agents/notes/implemented/feature/2026-06-30-interception-extension-points.md`（拦截扩展点 = 裁决接缝）· `.../2026-07-06-approval-seam.md`（审批接缝）· `.agents/notes/implemented/architecture/2026-06-30-event-domain-semantics.md`（事件三域语义） |

---

## 8. 待你拍板的决策点

1. **结合形态**：确认走 **A（评测层）→ B（执行层）**，还是只看方案先不动手？
2. **LLM 通道**：DSH 侧用 `deepseek-official` + `zai` 内置路由，还是统一走你现有的 OpenAI 兼容网关（集中管理 key，少一个配置面）？
3. **裁决落点**：三关校验做进 `tools/pre-execute`（推荐，不侵入 loop），还是替换整个 `ctx.agentLoop`？
4. **运行环境**：DSH 跑在 Windows 原生（Node 22.22.2 已满足），还是进你的 Docker compose（新增一个 `rag_dsh` 服务）？
5. **是否立刻做阶段 0**（纯环境验证，零风险）？

---

*本文与 `docs/design.md` / `docs/requirement.md` / `docs/test-metrics.md` 尚未产生约束关系——一旦进入实现，需按三文档联动约定同步更新。*
