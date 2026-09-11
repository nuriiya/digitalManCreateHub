# 测试指标（Test Metrics）

> **文档版本**：v1.2（2026-09-11）
> 本文件定义本平台**所有可量化验证口径**：指标定义、计算公式、判定阈值、当前工具落点与达成目标。
> 追溯：每个指标标注对应需求（`docs/requirement.md`）与设计章节（`docs/design.md`）。指标/阈值变更须同步三文档。

## 指标总览

| 编号 | 指标族 | 用途 | 对应 requirement | 对应 design |
|---|---|---|---|---|
| T-A | 端到端验收指标（冒烟/质量门） | 提交前回归 | R-1 族、R-11 | §1 / §8 |
| T-B | Benchmark 四组对照（幻觉/拒答/准确率 + 边际） | 数字人本体质量 | R-5.1–R-5.4 | §4.1 |
| T-C | 能力题可执行验证（pass/fail/pass_rate） | 写代码能力 | R-5.5–R-5.7 | §4.2 |
| T-D | 训练师迭代（baseline / improvement） | 本体补训有效性 | R-5.8–R-5.9 | §4.3 |
| T-E | 考核（双 LLM 判别 pass/fail/missing） | 上岗许可 | R-5.10 | §4.4 |
| T-F | 调研置信度（数据质量分层） | 入库先验分级 | R-8 | §5.4 |
| T-G | 工程健壮性指标（DB/并发/恢复） | 平台稳定性 | R-9 | §6 |
| T-H | 消息协议一致性（DMP 收敛 / 校验 / 回退） | LLM 交互统一 | R-13 | §10 |
| T-I | 内容类型质量（type 覆盖 / 一致 / 强制注入） | 知识性质标注 | R-14 | §11 |
| T-J | 工作台体验（步数 / 按需加载 / 状态收敛） | 前端重构验收 | R-16 | §13 |
| T-K | DFMEA 链路（来源标注 / 越级代填 / 可运行） | 需求→DFMEA 编排 | R-17 | §15 |

---

## T-A 端到端验收指标（E2E Acceptance）

| 指标 | 定义/口径 | 阈值/目标 | 工具落点 |
|---|---|---|---|
| A-1 一键启动 | `start1.ps1`（dev，复用镜像）在既有镜像下无 build、秒起 | ≤30s 内 `:8000` 可登录 | start1.ps1 + curl 健康探针 |
| A-2 首屏可用 | 登录后 8 页可切换、无 console 报错 | 全绿 | 前端 devtools |
| A-3 文档摄取冒烟 | 上传一份 md → chunk 入库 → 检索命中原文 | 1 次端到端通过 | upload-files + search API |
| A-4 流式对话冒烟 | 发问 → 立即占位气泡 → 「由 X 组织语言」 → token 累积 → done | 全程可见、无死等 | /api/chat/stream + 前端 |
| A-5 前端 no-cache | HTML 响应带 `Cache-Control: no-cache` | 每次 F5 拉新 index | curl -I / |
| A-6 静态资源不缓存污染 | JS/CSS 带 `max-age=300`，hash 变化即 miss | 换 build 后 F5 生效 | curl -I /assets/* |

## T-B Benchmark 四组对照（G0–G3）

**四臂定义**（同一批题）：G0 `none`（裸模型，无 RAG 无本体）/ G1 `ontology`（仅本体，动态检索窗口）/ G2 `rag`（仅 RAG top3 原文）/ G3 `rag_ontology`（叠加）。

### B-1 判定类别（verdict，闭集）

| verdict | 含义 | 判卷人 |
|---|---|---|
| `correct` | 与标准答案一致 | GLM 提名 + 确定性终审 |
| `partial` | 部分正确 | 同上 |
| `wrong` | 答错或编造（幻觉） | 同上 |
| `refused` | 明确拒答（如「我不知道」） | **确定性拒答词检测可覆盖 GLM** |

确定性终审规则：闭集外一律 `wrong`；回复含拒答标记 → 覆盖为 `refused`（诚实拒答算拒答不算编造）。

### B-2 核心指标（每臂独立统计，`_stats()`）

| 指标 | 公式 | 意义 |
|---|---|---|
| 准确率 accuracy | `correct / n × 100%` | 答对比例（主指标） |
| 幻觉率 hallucination | `wrong / n × 100%` | 编造/答错比例（**要压低**） |
| 拒答率 refusal | `refused / n × 100%` | 诚实拒答比例（不得为零，也过高说明覆盖差） |
| partial | 单列百分比 | 部分对（不并进 correct） |

### B-3 边际指标（百分比点 pp，判断注入增益）

| 指标 | 公式 | 含义 |
|---|---|---|
| ontology_pp | `G3.accuracy − G2.accuracy` | 本体约束带来的独立增益 |
| rag_pp | `G3.accuracy − G1.accuracy` | RAG 参考带来的独立增益 |
| total_pp | `G3.accuracy − G0.accuracy` | 全链路总增益 |

结论模板（代码生成，0 LLM）：按 accuracy 排序四臂 + 幻觉最优臂 + `本体边际 … · RAG边际 … · 总增益 …`。

### B-4 目标基线（达成判定）

| 判定 | 条件 |
|---|---|
| 通过 | G3 accuracy ≥ G0；ontology_pp ≥ 0；rag_pp ≥ 0；G1/G3 幻觉率不高于 G0 |
| 本体待修 | 任一臂幻觉率高于裸模型 → 触发归因（R-5.3） |
| 数据/题集异常 | questions = 0 或四臂跑挂 → benchmark 标 failed |

默认样本：15 题（上限 30），四组共用同批题，本地 ollama 低温单次采样。

## T-C 能力题可执行验证（Capability / Executable Test）

**判定铁律**：代码对不对由容器跑测试说了算（LLM 不判）。

| 指标 | 定义 | 阈值/目标 |
|---|---|---|
| C-1 verdict | 容器执行 assert：`pass`（绿）或 `fail`（红/超时/异常） | 唯一二进制判定 |
| C-2 pass_rate | `pass 运行数 / 总运行数` | 逐数字人 / 逐角色统计 |
| C-3 反应式命中 | reactive 模式下 ≤3 轮内 `fail → 修改 → pass` | 记录 `rounds`（写了第几轮过） |
| C-4 沙箱合规 | 容器带 network_disabled / read_only / cap_drop / 资源限额 | 100% 运行强制 |

题源：HumanEval 164（可执行真值）+ 项目相关题（capability_tasks.json / capability_tasks_project.json）。

**消融矩阵实证基线**（12 道项目题 × qwen2.5:7b-32k，2026-09-09 五组）：
| 组 | 形态 | pass | 说明 |
|---|---|---|---|
| A | 单数字人 + 本体注入 | **9/12** | 当前最优；本体规则即考题答案 |
| B | 单数字人 − 本体 | 7/12 | 本体轴增益 +2 |
| C | pipeline（writer↔reviewer）+ 本体 | 7/12 | reviewer 同模型转述有损 |
| D | pipeline − 本体 | 7/12 | 与 B 持平，轮次 3→2 微改善 |
| E | pipeline + 本体 + **reviewer 喂饱**（docstring+断言+全量输出） | 7/12 | R1 诊断质量↑，writer 二次修正成新瓶颈 |

结论：reviewer 信息饥饿非唯一瓶颈（E 不升）；瓶颈已转移到 writer 读诊断后的修正环节，
需 writer 修正接入上下文或 reviewer 直接产补丁（results_pipe1_fed/，design §5.3.2）。

**模型 × 形态 对照实证（2026-09-09 九组 · 同批 12 道项目题）**：
| 组 | 形态 | qwen2.5:7b-32k（r2 干净重跑） | **V4-Flash** | 解读 |
|---|---|---|---|---|
| A | 单数字人 + 本体 | **8/12**（9/12 上午批） | **11/12** | 本体注入在弱模型下收益最大（A−B 差在强模型趋平） |
| B | 单数字人 − 本体 | **7/12**（7/12） | **12/12** | V4 无本体也满分（规则已内化） |
| C | pipeline + 本体 | **7/12**（7/12） | **12/12** | qwen 下 C<A（7B 多跳转述损耗）；V4 下 C=A=满分 |
| D | pipeline − 本体 | **7/12**（7/12） | **12/12** | 同上 |

> **注（r2 重跑）**：下午修复 host.docker.internal 误走 LLM_PROXY（ollama 全 502）+ 截断续写
> 落地后，qwen 四组用干净链路重跑（results_qwen_r2_*，13min 无 ERR）。A=8/12、B/C/D=7/12，
> 与上午结论一致（A 含本体最优、pipeline 在 7B 下无增益）。上午批 A=9 的差异来自
> normalize_name/parse_dsn/dedup_tags/chunk_overlap 的模型随机抖动（A/B/C/D 四组交集 fail
> 恰好是 7B 稳定弱点：中文边界、端口省略歧义、多约束分段）。跨模型结论不变：
> **同 pipeline 流程 qwen C=7/12 → V4 C=12/12，瓶颈=模型能力而非机制**。

### 本体论有用性论证（Ontology Utility Case，T-C 消融结论）

> 消融数据是「本体注入 / pipeline 形态 / 模型能力」三者贡献的分离证据。结论如下
> （逐题 JSON 存 exports/test-suite-project-2026-09-09/results*，对话 log 见
> exports/test-suite-project-2026-09-09/*.log）：

1. **本体注入的收益 = 把「规则写得像考题答案」放进 system**（design §5.3.1/§4.2）：
   - qwen 下 A−B = 9−7 = **+2 题**，且翻盘题正是本体里写死规则的
     （dedup_tags「去重返回规范形式」、merge_summaries「去重键与保留文本分离」、
     safe_truncate「省略号不计 max_chars」）——注入即得分。
   - **收益随模型增强而趋平**：V4 下 A=11、B=12（−本体反超 1 题），说明强模型已
     内化通用编码规范，本体注入的边际价值在"模型不会但平台强约束必须"的规则上
     （如领域专属契约、平台铁律），而非通用编程常识。
   - **结论（平台层）**：代码型数字人的本体应写成**可执行编码规范（规则即考题答案）**；
     生产用 V4-Flash 时本体重点放平台领域约束，弱模型（7B）部署时本体含通用规则
     仍有 +2 确定性收益。
2. **pipeline 协作形态本身成立，其"无增益"是 7B 模型能力的假象**：
   - qwen：C/D(7) < A(9)——writer→reviewer→writer 多跳转述在 7B 下损耗（extract_code
     三轮写崩为 61~73 字符空壳，SyntaxError，log 见 *qwen-e-extract_code.log）；
   - **V4-Flash：C=D=A=满分（12/12）**——同流程换强模型即满格，证明机制无缺陷，
     瓶颈在模型上下文/长输出修正能力（design §9.5 截断续生成为本地 7B 降级链路）。
3. **能力题判定与回归基线**：沙箱可执行验证（LLM 不判分）是可靠真值；qwen2.5:7b
   保持为低成本回归/对照基线，生产写码通道默认 V4-Flash（capability provider=llm）。

## T-D 训练师迭代（Trainer）

| 指标 | 定义 | 用途 |
|---|---|---|
| D-1 baseline pass_rate | 迭代前跑分（默认 samples=1；可多数票降噪） | 参照系 |
| D-2 after pass_rate | 补本体装配后复测跑分 | 对比 |
| D-3 improvement | `after_rate − base_rate`（pp） | **正 = 补对了本体**；≤0 = 本轮无效，检查提名质量 |

多数票规则：samples>1 时，`pass 票 > 总票/2` 才判该题 pass（降 LLM 随机性）。

## T-E 考核（Exam / 双 LLM）

| 指标 | 定义 | 说明 |
|---|---|---|
| E-1 verdict | `pass` / `fail` / `missing` | 建议态，用户终审 |
| E-2 通道约束 | LLM-1 闭卷 → LLM-2 异源开卷（GLM）→ 确定性三关 | **判别器未配置 → 直接失败**（禁止同源自评） |
| E-3 判别通道禁 thinking | judge 通道 `thinking: disabled`（纯提名，防超长思考链） | 降时延、防断连 |

## T-F 调研置信度（数据质量分层）

| 级别 | 分值 | 来源 |
|---|---|---|
| 期刊 | 100 | DBLP/OpenAlex type=journal |
| 会议 | 80 | type=conference |
| 预印本 | 60 | preprint（arXiv 等） |
| 学位论文 | 40 | dissertation |
| 网页 | 0 起 | 其余（不优先） |

排序 = `confidence` 降序 + 次级键（确定性代码裁决，非 LLM）；聚合去重后 top 结果入库 RAG（`ingest_results`），入库 tags 含 `confidence_level`。

## T-G 工程健壮性

| 指标 | 定义 | 阈值/目标 |
|---|---|---|
| G-1 摄取原子性 | 每 chunk 一个 PG 事务（行+进度+checkpoint） | 断电最多重做 1 chunk |
| G-2 三方互斥 | scan/repair/ontology 同刻仅一个 job | 闸门命中即拒绝并发 |
| G-3 并发自适应 | 提取 worker 默认 4；失败 -1（下限 1）；连 8 成功 +1 | 429 重排队 ≤3；非 429 LLMError auto_pause |
| G-4 DB 连接 | threading.local 每线程一连接；失败无条件 rollback；OperationalError 自动重连 | 无跨线程共享连接 |
| G-5 事件流恢复 | WS 断线指数退避 + seq 增量补齐 | 前端重连不丢事件 |
| G-6 埋点/日志 | uvicorn 标准输出捕获于 data/uvicorn*.log | 启动/请求全留痕 |

## T-H 消息协议一致性（DMP）

| 指标 | 定义/口径 | 阈值/目标 | 工具落点 |
|---|---|---|---|
| H-1 解析器收敛度 | 私有 JSON 解析器数量 | **4 → 1**（`protocol.parse`） | 代码检索 + 单测 |
| H-2 envelope 覆盖率 | 进出 LLM 的消息为 DMP envelope 的占比（对话/能力题/抽取/分诊/设计/归因/考核） | 100% | 协议层埋点 |
| H-3 schema 校验通过率 | `structured()` 首轮通过 / 总调用 | 降级链 ≥95%；**终审链 ≥99%（否则显式失败）** | 日志统计 |
| H-4 legacy 兼容率 | 旧纯文本会话可无损回放比例 | 100% | `protocol.parse` legacy 分支单测 |
| H-5 流式解码正确率 | `StreamJsonReader` 增量 delta 拼接 == 最终 JSON 的 `payload.text` | 100%（失败降级不丢内容） | 单测 + 前端比对 |
| H-6 回退可用性 | `DMP_MODE=off` 后主链功能完整 | 全绿（8 页可切换 + 对话可用） | 冒烟 |

## T-I 内容类型质量（chunk type）

> **已落地（2026-09-11，design §11）**。验证工具：
> `docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend python scripts/verify_chunk_types.py`
> （DDL / 词表 seed / `resolve` 裁决 / 入参归一化）· `scripts/verify_chunk_types_flow.py`
> （写路径 · 读路径 · 列表过滤 · 检索过滤 · 两路注入，跑完自动回滚）·
> `scripts/verify_search_split.py`（两路检索共享一次 embedding + 两组不重叠）。
> 三者容器内全绿（自审修复见 design §11.8）。

| 指标 | 定义/口径 | 阈值/目标 |
|---|---|---|
| I-1 type 覆盖率 | 非 `unknown` 的 chunk / 总 chunk | ≥85%（词表覆盖度） |
| I-2 判定一致率 | LLM 提名 type 与人工标注一致 / 抽检 50 条 | ≥80% |
| I-3 两维度一致性 | chunk 两维度 == **落库时**词表默认值（写入快照；改词表不回溯存量） | **100%**（确定性裁决，硬约束） |
| I-4 强制注入配额 | `mandatory=2` 不参与普通 top-K 竞争，走独立配额（上限 `RAG_RULES_MAX=4`） | **100%** 进上下文（配额内） |
| I-5 词表自定义生效 | 新增/改词表后，新 chunk 按新默认值落库 | 即时（延迟 0） |
| I-6 引用完整性 | 删除被 chunk 引用的 type 被拒绝 | **100%** |
| I-7 检索开销 | 对话两路注入的 embedding 调用次数 | **= 1**（共享查询向量） |

## T-K DFMEA 自动编排链路（design §15）

> 该链路当前**全部未实现**（缺口 21 项，见 design §15.3）；以下阈值是落地后的验收口径。

| 指标 | 定义/口径 | 阈值/目标 |
|---|---|---|
| K-1 来源覆盖率 | 带来源标注的单元格 / 总单元格 | **100%**（无来源即不合格） |
| K-2 `ai_new` 可筛出率 | 标记 `ai_new` 的格数 == 人工复核清单条数 | **100%** |
| K-3 越级代填率 | 前级已有证据、却被后级来源填写的格数 | **0** |
| K-4 证据可回溯源 | `history` / `table` 来源的格能回指到具体表行或 chunk | 100% |
| K-5 流水线可运行率 | 自动生成的 DFMEA pipeline 能跑完且节点不空转 | 100%（**当前 0**：节点 persona_id 全为 None） |
| K-6 ask 回退成功率 | 专家数字人回答被成功回注到 DFMEA 工程师的比例 | ≥95% |

## T-J 工作台体验（IdentityWorkbench）

> **已落地（2026-09-11，design §13）**。当前达成：J-1 ✅ / J-2 ✅ / J-3 ✅ / J-4 ✅ /
> J-5 ✅ / J-6 ✅。其中 J-3 与 J-4 为 2026-09-11 补做（`hooks/useWorkbench.ts`：
> 状态收敛 + 只加载选中项；组件内 `useState` 归零）。

| 指标 | 定义/口径 | 阈值/目标 | 对照（旧） |
|---|---|---|---|
| J-1 创建步数 | 进入页面 → 提交创建的最少点击 | **≤3**（向导三步） | 旧：跨 tab + 靠文字指路 |
| J-2 装配生效步数 | 装配并入池的最少点击 | **≤2** | 旧：5 步 + 手工等 job |
| J-3 按需加载 | 未打开数字人时发出的详情请求数 | **0** | 旧：4 个 useEffect 常驻 + 5s 轮询 |
| J-4 状态收敛 | 工作台组件内 `useState` 数量 | **≤3**（其余进 reducer） | 旧：23 |
| J-5 页面可用性 | 8 页可切换、无 console 报错 | 全绿（沿用 T-A A-2） | — |
| J-6 任务可见性 | 任意页面可开任务抽屉并看到运行中 job | 100% | 旧：独占一个 tab 整页 |

---

*维护约定：指标的任何变更（新增/改公式/改阈值）必须同时更新 requirement.md 对应 R-5 族条目与 design.md 相关章节（design.md §14）。*
