# 测试指标（Test Metrics）

> **文档版本**：v1.0（2026-09-09）
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

---

*维护约定：指标的任何变更（新增/改公式/改阈值）必须同时更新 requirement.md 对应 R-5 族条目与 design.md 相关章节（design.md §10）。*
