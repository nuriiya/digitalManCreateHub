# DeepSeek Harness（dsh）融合点研究

> 分支：`experiment/dsh-integration` · 日期：2026-09-15
> 源码：`D:\workspace\deepseek-harness`（github.com/deepseek-ai/deepseek-harness，MIT，v0.1 开发者预览）
> 目标：评估 dsh 有多少内容可以融入本项目（digitalManCreateHub），并给出落地顺序。

## 0. dsh 一句话画像

DeepSeek Harness 是 DeepSeek 开源的 agent 运行时底座（V4-Pro 的 Terminal-Bench /
DeepSWE 成绩即由其 Minimal 模式评出）。核心理念**一切皆插件**（Cordis 运行时：
插件向共享 context 认领服务键、注册均为可逆副作用）；53 个包覆盖 loop、tools、
session、sandbox、skill、mcp、subagent、compaction、plan、todo、guard 等。
TypeScript/pnpm 技术栈——**我们学它的设计，不搬它的代码**（本项目是 Python/FastAPI）。

## 1. 融合点总览（按价值排序）

| # | dsh 机制 | 本项目现状 | 融合价值 | 等级 |
|---|---|---|---|---|
| 1 | **append-only 会话事件日志**：消息历史从事件派生、从不单独存储、从不覆盖；resume/fork/replay 全建在同一事件流上 | events 表（llm.call/reply/pipeline.node）+ chat_messages **双轨且割裂**；chat_messages 会 UPDATE（进度回写就是改历史） | ★★★★★ | A+ |
| 2 | **turn/step 事件化循环**：step=一次模型调用+其工具；事件序列 step/start→assistant/message→tool/call→tool/result→step/end | chat.py 的 tool-use 循环是无结构 for + MAX_ACTION_ROUNDS 计数；对话页手风琴靠 pipeline.node 的 **seq 区间推断**归属 LLM 交互（hack） | ★★★★★ | A+ |
| 3 | **工具流水线瀑布**：tools/pre-execute→execute→post-execute，监听器可拦截/改写 | actions.py 已有 guard_action（守卫+执行两段） | ★★★★ | A |
| 4 | **Minimal 模式 = 评测控制变量**：压到最小工具集测模型裸能力 / 固定模型换插件组合测 harness | 遗留任务「benchmark.py 扩展为完整 agent harness（loop+tools 待补）」；WiFi 考试 G/R/V 三阶段 | ★★★★ | A |
| 5 | **system-prompt 片段化组装**：片段注册+按能力准入+声明合并扩展 | _system_prompt 顺序拼接（本体/铁律/动作清单/纪律注入） | ★★★ | A- |
| 6 | 沙箱三档（read-only/workspace-write/full）+ 文件 effect 策略 | Docker 沙箱（mcp-paper） | ★★★ | B |
| 7 | compaction（上下文压缩 seam） | 无（长对话直接堆 history） | ★★★ | B |
| 8 | subagent / 编排即插件（Standard/Code/Minimal/Creator 四模式=四种插件组合） | pipeline DAG + ask_expert + 复核门 | ★★（印证） | B |
| 9 | skill / mcp / guard / todo / plan 包 | 项目已有对应物（skill 体系 / mcp 沙箱 / guardians / 任务管理） | ★★（对齐） | B |
| 10 | Cordis 运行时本体 / Electron / web profile | — | 不引入 | C |

## 2. 三个 A+ 级融合点的具体推演

### 2.1 事件溯源：消灭「改历史」

**dsh 铁律**：SessionEvent 是唯一真源；消息历史从日志*派生*（derive）而来，从不
单独存储；回放=从同一组事件重新派生。审计/续跑/分叉/重放四种操作共享一条流。

**本项目违背处**：`updatePipelineProgress` 对 chat_messages 做 UPDATE（进度气泡
回写）；这拿到了「WorkBuddy 式持久化」，但**丢失了中间态**——重启/出错/多写都
不可回放。events 表倒是 append-only，但它只记 job 维度，不含对话维度。

**融合方向**：
- chat_messages 只追加：进度更新 = 追加一条 `progress` 事件消息（或统一并入
  events 表，加 session_id 维度），渲染层取「该 assistant 消息的最新 progress 派生态」
- 消息列表接口 = 事件流的**派生投影**（fold），而非直接 SELECT
- 收益：天然获得对话级 replay/fork（换 system prompt 重跑一轮实验 = fork 一条轨迹），
  这正是 dsh 社区反应最热烈的能力，也直接服务于「auto-iterate 训练器」

### 2.2 turn/step 事件化：手风琴的 正道

现在的手风琴靠 `pipeline.node(running)` 的 seq 区间**推断** LLM 事件归属——能用
但脆（事件缺 seq 字段时归错）。dsh 的 step 模型把归属变成**显式**：

```
step/start → request/header → user/message → assistant/message →
tool/call → tool/result* → step/end
```

**融合方向**：数字人 tool-use 循环（chat.py 的 for attempt + _parse_tool_calls）
也发同构事件：`step/start {identity, round}` / `tool/call {name,args}` /
`tool/result {ok,payload}` / `step/end`。前端手风琴与「任务与事件」页直接按
step 分组消费，不需要区间推断；且普通对话（非 pipeline）也能获得同款手风琴。

### 2.3 Minimal 模式思想 → agent harness 升级

dsh 官方用 Minimal 模式（一个 shell + 一个编辑器）评出 V4-Pro 的
Terminal-Bench——**把「评测的对照组」做成了产品功能**。

**融合方向**（对应遗留任务 benchmark.py → 完整 harness）：
- **固定 harness 变量测模型**：Minimal 配置（最小动作集+固定 prompt 模板）跑
  数字人能力考试（如 WiFi 考试的 G/R/V），分数差异只反映模型（DeepSeek vs GLM）
- **固定模型测 harness**：同一考试，切换动作集/纪律注入的开与关 → 消融实验
  （「执行触发纪律」到底值多少分，量化而非直觉）
- 落点：`benchmark.py` 增加 `--profile minimal|full` + `--ablate discipline|actions`

## 3. 落地顺序建议（本分支实验路线）

1. **E1 事件化 tool-use 循环**（最小改动）：chat.py 循环内发 step/tool 事件 →
   手风琴改按 step 分组（去区间推断）。验证：普通对话也有手风琴。
2. **E2 对话事件溯源**：chat_messages 追加化 + 派生投影；进度回写改追加。
   验证：fork 一条历史轨迹改一个变量重跑（auto-iterate 的底座）。
3. **E3 工具流水线 pre/post 钩子**：guard_action 扩为 pre-execute（守卫+审批门+
   遥测）/ post-execute（来源闭集校验已在这里，扩展结果改写）。
4. **E4 harness 消融**：benchmark.py 的 profile + ablate 开关，跑一组
   「纪律注入开/关」对照，产出定量报告（用户偏好：A/B 对比 + KPI）。

每步独立可交付，失败可单步回滚；不动 §14 编号冻结（新设计从 §15 续）。

## 4. 不引入清单（及理由）

- **Cordis/TS 运行时**：语言异构；「服务键发现+可逆副作用」的思想用 Python 的
  抽象（registry + contextmanager）等价表达即可
- **Electron 桌面 / web profile 组合体系**：已有 vite 前端 + start 脚本体系
- **dsh 的 benchmark 目录**：它只含 4 个流稳定性基准（重连/长会话/折叠），与
  我们的领域考试无关

## 5. 参考索引

- 架构：`deepseek-harness/docs/architecture.zh.md`（轮次流程、事件域、会话日志）
- 会话：`docs/subsystems/session.zh.md`（SessionEventMap 全词汇）
- 工具：`docs/subsystems/tools.zh.md`（ToolDefinition/瀑布流水线）
- 事件溯源的意义：腾讯云社区《DeepSeek Harness Agent 框架详解》
  （append-only 日志 + resume/fork/search/replay 四操作共享一条事件流）
