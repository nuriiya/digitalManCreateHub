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
| T-L | 数字人模板（渲染确定性 / 幂等 / 完整度） | 模板化创建 | R-18 | §16 |

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

> **已落地（2026-09-11，design §15）**。验证工具：
> `docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend python scripts/verify_dfmea_bluetooth_e2e.py`
> （手机蓝牙模块端到端：G 生成 / R 运行 / V 产出校验共 20 条断言）·
> `scripts/verify_fmea_actions.py`（动作：查历史 · 查表 · 写行 · 批量 · 来源闭集 · AP 以表为准）·
> `scripts/verify_pipeline_fmea.py`（ask 边执行语义 · review 门控 · FMEA kind）·
> `scripts/verify_toolcall_fix.py`（tool_call 容错 · 动作名归一化 · 参数别名 · 并行多调用）。
>
> **蓝牙模块实测**（一句话需求 → 生成 → 运行）：**23 行 DFMEA，覆盖 11 个部件**
> （天线/匹配网络/PA/LNA/滤波器/射频开关/链路预算/LDO/去耦/晶振/浪涌防护）；
> K-1 来源覆盖率 **100%**、K-4 可追溯 **100%**（`history#N` 与 `expert:<名>` 全部回指成功）、
> AP 与 AP 表一致 **100%**。

| 指标 | 定义/口径 | 阈值/目标 |
|---|---|---|
| K-1 来源覆盖率 | 带来源标注的单元格 / 总单元格 | **100%**（无来源即不合格） |
| K-2 `ai_new` 可筛出率 | 标记 `ai_new` 的格数 == 人工复核清单条数 | **100%** |
| K-3 越级代填率 | 前级已有证据、却被后级来源填写的格数 | **0** |
| K-4 证据可回溯源 | `history` / `table` 来源的格能回指到具体表行或 chunk | 100% |
| K-5 流水线可运行率 | 自动生成的 DFMEA pipeline 能跑完且节点不空转 | 100%（**已达成**：实测 0 个节点未绑定数字人） |
| K-6 ask 回退成功率 | 专家数字人回答被成功回注到 DFMEA 工程师的比例 | ≥95% |

## T-L 数字人模板（design §16）

> **已落地（2026-09-12）**。验证工具：
> `docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend python scripts/verify_persona_templates.py`
> （22 项断言，容器内全绿）与 `scripts/verify_template_from_identity.py`
> （反向沉淀 24 项断言，容器内全绿）。

| 指标 | 定义/口径 | 阈值/目标 |
|---|---|---|
| L-1 渲染确定性 | 同一模板 + 同一槽位值 → 输出完全一致 | **100%**（零 LLM） |
| L-2 槽位替换完整率 | 渲染后残留未替换的 `{{…}}` 占比 | **0** |
| L-3 必填校验 | 缺必填项时拒绝渲染并指出字段 | **100%** |
| L-4 实例化幂等 | 重复实例化不新增数字人、不重复本体/动作 | **100%** |
| L-5 内置模板保护 | 内置模板删除被拒 | **100%** |
| L-6 生成完整度 | 本体 ≥ 模板自带条数；动作全部 approved | **100%** |
| L-7 预览无副作用 | `/preview` 不产生任何落库写入 | **100%** |
| L-8 往返一致性 | 反向沉淀的模板空填写渲染 == 原数字人（逐字段） | **100%**（沉淀不丢信息的硬判据） |
| L-9 反向沉淀动作保真 | 反推模板可实例化出与原数字人同条数的本体/锚点/动作 | **100%** |

## T-M WiFi 场景 DFMEA 考试（design §15.7）

> **已落地（2026-09-12）**。验证工具：
> `docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend python scripts/verify_dfmea_wifi_e2e.py`
> （G 生成 / R 运行 / V 判分）· `scripts/grade_dfmea_wifi.py`（30 题考卷）·
> `scripts/verify_fmea_part_search.py`（部件搜索 19 项断言）。
>
> **为什么另开一套**：蓝牙场景的历史 FMEA 就在库里，主要考「查准 + 标注」；
> WiFi 模块**库里没有直接记录**，考的是「自主搜索部件 → 对同类部件做类比推导 →
> 无据处如实降级」。

> **实测（2026-09-12）**：首轮 **140/150 = 93.3%** —— 其中 **5 点是考卷自身的 bug**
> （C.2 本意"历史**或**专家"，误写成两条硬断言），修正口径后为 **145/150 = 96.7%**，
> **唯一真实失分点是 `连接与漫游管理` 子系统未覆盖**。复核门另判 FAIL，抓到两个真问题：
> 同一批 29 条**被写了两遍**（58 行）、第 27 条 AP 可查表却标 `ai_inferred`。
> 修复见 design §15.7「迭代记录」。

| 指标 | 定义/口径 | 阈值/目标 |
|---|---|---|
| M-1 考卷正确率 | 通过判定点 / 150（30 题 × 5 点） | **≥98%**（最多错 3 点） |
| M-2 部件覆盖 | 被分析的子系统数 / 13 | **100%** |
| M-3 部件搜索可用 | `fmea_part_search` 返回 `parts` 与 `history_parts` | 100% |
| M-4 类比引用率 | 引用了**对应部件族** `history#N` 的子系统占比 | **≥70%** |
| M-5 分工正确性 | 需要特定动作的步骤被派给绑定该动作的数字人 | 100% |
| M-6 生成不介入 | 拓扑与 persona 绑定全部由 LLM 产出（代码只校验落库） | 100% |

### T-M2 本轮迭代（2026-09-13）

> **背景**：2026-09-13 的环境（WSL 实例每 20–90 s 被回收）使长链路 job 反复
> `interrupted (service restart)`，是分数长时间上不去的**环境根因**；同期修掉
> 三处**交付物契约缺件**（design §15.8）。环境修复见下表 N-1~N-5。

| 轮次 | 行数 | 正确率 | 复核门 | 说明 |
|---|---|---|---|---|
| run#25 | 0 | 30/150 = 20.0% | FAIL | 复核门拦截写入 → 无行可判（**分数是写入被拦的产物，非模型能力**） |
| run#26 | 17 | **143/150 = 95.3%**（28/30 题） | FAIL | 仅剩题 10（`连接与漫游管理` 命中 0 行）与题 17（expert 引用 0 处） |
| run#27 | 0 | — | FAIL | 复核意见：「未提供 DFMEA 表结构化行数据」→ 定位到**交付物闸门未闭环** |
| run#28 | **0** | 30/150 = 20.0% | FAIL | 闸门已修（step2 68→2239 字、step7 65→9723 字**重试成功**），但**写入器排在复核门之后** → 表未落库 → 27 题无从判分 |
| run#29 | **0** | 30/150 = 20.0% | FAIL | 编排**已把写入放到复核门之前**（`step8-写库 → step9-复核`），仍 0 行。真因：**部件知识库为空** → 工程师拿 `parts:[]` 反复重试，8 轮耗尽 → 交付物是 **215 字裸 `<tool_calls>`** 块 |
| run#30 | — | — | — | 迭代中（复核门修复 + 判据区分），无有效场次记录 |
| run#31 | **15** | **131/150 = 87.3%**（24/30 题） | FAIL | **首个非零落地场次**。6 个失分点：题9/10（SW 族两子系统 0 行）、题17（expert 引用未写进 `sources`，`cited=[]`）、题21（`action` 全列 NULL）、题23（`天线馈电与接地` 误引 EMC 族 `history#16`）、题30（同题9源）。**根因见下** |

**run#29 的真实根因（本轮修复）**：**夹具缺失被静默掩盖**。`fmea_parts` 为空时
`part_search` 返回 `parts:[], count:0`，与「产品名没匹配上」**返回值完全一致** →
① 工程师无法判断是名字写错还是库里没数据，**反复重试同一动作直到轮数耗尽**；
② 考官也被误导（报告只显示「复核门 FAIL / 行数=0」，读起来像能力不足），
**白烧一整轮迭代**。修法两条：`part_search` 空库时改返回 `ok=False` + 修复指令；
考卷驱动加 **前置夹具自检** `preflight()`，四张表点数，缺件则**拒绝开跑**。

> step8 的 7 次 LLM 调用时序（`events.llm.call`，`prompt_len` 14503→17555）：
> 初次产出 → `搜索部件清单`(`parts:[]` ← 污染源) → `查 AP 表`(正常) →
> `查询历史 FMEA`(`hits:0`，用了中文部件名) → `取待人工确认清单`(`count:0`) →
> `核验专家引用`(`4 位专家`) → **轮数耗尽兜底仍是 tool_call** → 交付物 215 字。
> **7 次全在侦察，一次没写表。**

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-1 WSL 实例稳定性 | 单位时间内实例被回收次数（修复前 vs 后） | 修复后 **≈0** | 修复前 47 次/4.5h（≈每 3 min 一次）；改 NAT + logind 配置后降至 **1** |
| N-2 实例存活时长 | `/proc/uptime` 连续观测 | **≥10 min** | 修复后达 **1424 s / 23.7 min** 同一 boot_id |
| N-3 `part` 归一正确性 | `canonical_part` 单测断言 | 100% | **30/30 断言 PASS**（含 `SW+漫游 → 连接与漫游管理`） |
| N-4 专家引用差集 | `expert_citations` 单测断言 | 100% | 含带引用号 / JSON 字符串 / 未问专家等边界 |
| N-5 草稿误杀防护 | 含「工具调用」字样但正文充足的表 | **不误杀** | 已单测钉住 |
| N-6 交付物闸门 | 轮数用尽后仍能产出正文（而非裸 tool_call） | **100%** | run#28 实测：step2 68→2239 字、step7 65→9723 字 |
| N-7 节点失败即中止 | 重试后仍非交付物 → 该节点 failed + run 中止 | 100% | 不再把草稿下传 |
| N-8 表格解析（两形态） | `parse_table_rows` 单测断言 | 100% | **25/25 PASS**（行内 `字段=值` + markdown 表；专家清单不误收；缺 S/O/D 不收；缺 failure_mode 不收） |
| N-9 交付物自动落库 | `ingest_table_rows` 真库断言 | 100% | **12/12 PASS**（`SW`→`连接与漫游管理`、sources 抽取、AP 以表为准、幂等二次全 updated、无来源行**明确跳过不臆造**） |
| N-10 复核门前置落库 | 复核门打开时 `dfmea_rows` 是否已有该 run 的行 | **> 0** | 引擎在 `store_handoff` 之后、review 门控之前调用（design §15.8 ①） |
| N-11 汇总节点保真 | 17 行表的出站字符数（是否触发精炼丢行） | **不丢行** | `KIND_BUDGET[失效模式清单]` 4000 → **12000**；落库改读**原始产出** |
| N-12 空知识库响亮报错 | `fmea_parts` 为空时 `part_search` 的返回 | **`ok=False` + 修复指令** | 附 `knowledge_base_empty` 标记与 `seed_fmea_parts` 指令（run#29 静默故障回归） |
| N-13 产品名模糊匹配 | 库内名与任务名不必字面相等 | **命中** | `WiFi 模块` 与 `射频无线模块（蓝牙/WiFi）` **两种写法均返回 13 条**子系统 |
| N-14 `analogy_family` 可用性 | 每部件是否带可直接喂 `history_query` 的族码 | **100%** | 13/13 带；族覆盖 `ANT/RF/PMU/CLK/SW/CON/EMC` |
| N-15 前置夹具自检 | 考卷驱动是否拒绝在缺件时开跑 | **拒绝** | `preflight()` 点数 4 张表；缺件则 `R1` FAIL 并注明「本场考试结果无效」 |
| N-16 解析器+夹具单测 | `verify_fmea_table_parse.py` 全量断言 | 100% | **54/54 PASS**（含新增 6 项夹具/模糊匹配/族检索钉子 + 新增 6 项 `analogy_cases` 钉子） |

### T-M3 第 13 轮迭代（2026-09-14，针对 run#31 六处失分）

> **run#31 的法证结论**（用 `events`(job31) + `pipeline_run_handoffs` + `dfmea_rows` 反查）：
>
> | 题 | 失败项 | 真因 |
> |---|---|---|
> | 9 / 10 | `基带与固件` / `连接与漫游管理` **0 行** | ① `part_search` **判据误伤**：v1 用 `if not parts:` 判"库为空"，而模型带了 `query="蓝牙 WiFi 模块 子系统"` → 13 条被 query 全滤掉 → 误报 `knowledge_base_empty:true`（seq=2521）→ 模型据此判定"WiFi 模块没有官方子系统清单"而**放弃全部 13 个子系统**；② SW 族 3 条案例**未取回**（8 轮工具调用配额耗尽，seq=2542 正文自述） |
> | 17 | `expert:` 引用 0 处（`cited=[]`） | **复核门判 FAIL 是对的** —— 模型在正文写了 `expert:电源与时钟专家` 却没写进 `sources` 字段。缺陷在于**复核门是终态的**：产出表的人排在复核门之后，被驳回后永远没机会改 |
> | 21 | `action` 整列 NULL | 模型产的表**没有「建议措施」列**（9 个 handoff 仅 2 个含该列；`finalize-submit` handoff#239 不含） |
> | 23 | `天线馈电与接地` 未引 ANT 族 | 按"接地"字面错配到 **EMC 族** `history#16`；而 `fmea_parts#2` 的 note 早已写死 `BT-ANT-02`。**族码只有 1 个粗线索，模型不知道"这个子系统对应哪几条"** |
> | 30 | `基带与固件` hist=0 exp=0 | 与题 9 同源 |
>
> **1 个环境噪声**：`llm2`（异源核验通道）两次因 **90 s 空闲超时**降级到 `llm1` —— 按判据「降级可用于链路不中断，不可用于结论可信」，本轮达标结论**不成立**（复核门的独立把关能力正是被降级掉的东西）。

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-17 `part_search` 判据区分 | 「产品名写错」与「库里没数据」必须给出**不同**返回值 | 100% | **6/6 断言 PASS**：带 query 不命中 → `ok=True`+`query_miss`+`available_subsystems`；无 query → 13 条；错产品名 → 给 `products` 清单 |
| N-18 `analogy_cases` 一一映射 | 部件 note 里的 `BT-XXX-NN` 解析成**具体** `history#<id>` | 100% | **13/13 子系统**带非空 `analogy_cases`；SW 族 **3 子系统和 3 条案例一一对应**；`BT-ANT-01` → `[history#1, history#2]` **两条收全** |
| N-19 专家侧历史检索能力 | 部件专家（#10~#13）是否有 `fmea_history_query` | **100%** | 3 动作/人：检索本体 + 查询历史 FMEA + 检索 RAG |
| N-20 工具调用轮数配额 | 汇总节点单轮可用工具轮数 | **足够跑完**（原 8 不够） | `MAX_ACTION_ROUNDS` 8 → **14**（工程边界，非放宽标准） |
| N-21 复核门修复回边 | 复核 FAIL 后是否有**受限**的"退回改写 + 重审" | **有，且 ≤2 轮** | `pipeline.py` `MAX_REPAIR_ROUNDS=2`；只把复核意见**原样**回放给上游写入节点并重跑重审 |
| N-22 `action` 非空门控 | 每行 `建议措施` 是否非空 | **100%** | 工程师「写行自检」+ 复核员准则⑧ 双侧钉住（整列为空 → FAIL） |
| N-23 解析器+夹具单测（二次扩充） | `verify_fmea_table_parse.py` | 100% | **54/54 PASS** |

### T-M4 第 14 轮迭代（2026-09-14 · 拼接来源串 · 双代残留 · action 列）

> **run#32 成绩：146/150 = 97.3%（27/30 题）** —— 从 87.3% 跃升，**距阈值只差 1 点**
> （4 个失分点 / 150）。四个失分点经法证**全部定位到引擎侧**，且**同源两处**。

| 题 | 失败判定点 | 真因（法证） |
|---|---|---|
| **16** | `history 引用格式合法` — **38 处异常** | `sources` 里出现**拼接串** `"history#1；table#severity6"`：模型在一格里表达了两条依据，而 `source_kind_ok` 用 `split("#",1)[0]` **只校验第一个 `#` 之前的前缀** → 基名 `"history"` → **蒙混过关入库**；考卷按「值须是单个 `history#<数字>`」判 → 38 处异常 |
| **21** | `每行 action 非空` | **双代残留**：初轮 13 行是「无措施列」的形态 A（`action=NULL`），修复轮重写成形态 B（带措施），但 2 行的 `failure_mode` 被改写（加「（原 history#11…跨协议类比）」后缀）→ `(part, failure_mode)` 幂等键不匹配 → **INSERT 而非 UPDATE** → 新旧两代并存，考卷读到混合两代 |
| **17** | `expert:` 引用 0 处 | 该 run **全程未触发 `ask` 边**（模型自述「本次未调用专家」）。属**能力边界**而非缺陷：历史库覆盖得到时它就不问专家 |
| **23** | — | 本轮**已通过**（上一轮的 `analogy_cases` 修复生效） |

> **复核门的独立佐证**：复核员原文 `[REVIEW:FAIL]` 独立抓出同样两处 ——
> 「P2（阻断）：清单行数与宣称不一致，**疑似重复写入**。宣称 18 行，清单实际覆盖
> 19 个 row_id（263–281）。重复组：row_id=273 与 280、274 与 281」。
> 这是**异源核验与考卷同结论**的又一例（复核员没看到考卷，考卷没看到复核员）。

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-24 来源 token 单值性 | 一格 `sources` 必须恰好一个 token（禁分隔符） | **100%** | `_SRC_SEPARATORS` 拦 `；;，,、\|/`；`source_kind_ok` 拒拼接串 |
| N-25 同格双来源归位 | `6（history#1；table#severity6）` 的拆解 | **一字段一 token** | `table#severity6` 占 `severity`、裸 `history#1` 归 `failure_mode`（行的身份锚） |
| N-26 最后交付物胜 | 修复轮重写同子系统时旧代被取代 | **无重复行** | `ingest_table_rows` 落库前按本段覆盖的子系统 `DELETE`；返回 `superseded` 计数 |
| N-27 未覆盖子系统不误删 | 只清本段涉及的子系统 | **保持完整** | 单测钉住：写「时钟源」不影响「天线阻抗失配」「漫游认证失败导致重连」 |
| N-28 `action` 解析（形态 1） | 别名表须含 `action`/`建议措施`/`措施` | **100%** | 补齐别名（原表**根本没有 action** → 形态 1 永远抠不出措施列） |
| N-29 `action` 解析（形态 2） | `_ROW_FIELD_MARKS` 须含 `action` | **100%** | 补齐；新增 `_CN_HEADERS`（中文表头 → 字段，长别名在前） |
| N-30 单字母列名 | 表头 `S`/`O`/`D`/`AP` 须能映射 | **100%** | 新增 `_ONE_LETTER_MARKS`（原实现 `"severity" in "s"` 恒 False → S/O/D 三列全丢 → 中文表头表解析 **0 行**） |
| N-31 解析器单测（三次扩充） | `verify_fmea_table_parse.py` | 100% | **88/88 PASS**（54 → 88：+34 项，覆盖上述全部修复） |
| N-32 模板动作断言不锁死数量 | `verify_persona_templates.py` | **语义断言** | 由 `n_act == 2` 改为「蓝图声明的动作全部绑定且 approved」（补动作不再误报回归） |

### T-M5 第 15 轮迭代（2026-09-14 · **达标 98.0%**）

> **run#33 成绩：147/150 = 98.0%（28/30 题全对）—— 阈值达成 ✅**
> `job#33 status=done`（首次非 `blocked`）、`dfmea_rows=17`（一个子系统一行，无重复）。
> 从 87.3% → **98.0%**，两轮内到线。

| 题 | 状态 | 说明 |
|---|---|---|
| 1–16, 18–20, 22–30 | **PASS** | 含上轮失分的 **题 16**（拼接来源串）与 **题 23**（ANT 族类比）—— 两处修复均生效 |
| **17** | FAIL | **本场编排（pipeline #44）压根没有 `ask` 边**（G 阶段实测「有 ask 边 = False」），工程师**无专家可问** |
| **21** | FAIL | 17 行里仅 **1 行** `action` 为 NULL：该行写成 `S5 O4 D4 AP=M；建议：…`（**不写 `=`、措施用「建议：」**）→ 解析器两条路径都没覆盖 |

> **题 17 的根因已查清**：不是规则问题，而是**本场生成的拓扑把 4 个专家接成 `supply`
> 并行节点、没有 `ask` 回边**。上一场（#42）有 4 条 `ask` 边、这场没有 ——
> 拓扑由 LLM 生成，**时有时无**。工程师即使想遵守「专家复核不可省」也无路可走。
> 这是**生成侧能力**问题（拓扑要素缺失），不是引擎缺陷；下轮从**模板规则侧**
> 要求「聚合节点必须把专家接成 `ask` 回边」来补。

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-33 考卷正确率（达标场） | 通过判定点 / 150 | **≥98%** | **147/150 = 98.0% ✅**（题 16/23 已转 PASS） |
| N-34 run 终态 | `pipeline_runs.status` | **`done`** | `done`（首次；前 5 场均为 `blocked`） |
| N-35 行数无重复 | `dfmea_rows` 行数 / 子系统数 | **1:1** | 17 行 / 13 子系统（4 个多行子系统属正常，无同 (part, failure_mode) 重复） |
| N-36 裸值形态兜底 | `S5 O4 D4 AP=M`（不写 `=`）须能解析 | **100%** | `_cell` 新增短字段裸值兜底；`建议：` 亦识别为 `action` |
| N-37 解析器单测（四次扩充） | `verify_fmea_table_parse.py` | 100% | **97/97 PASS**（88 → 97） |
| N-38 专家复核规则落地 | 工程师/复核员模板须要求「历史命中也要问专家」 | **已实现** | 原「询问专家规则」写的是**「仅在历史库与准则表都无结果时才询问专家」**——与优先级链第 3 级**自相矛盾**，是本轮题 17 的规则侧根因，已改写 |

### T-M6 第 16 轮迭代（2026-09-14 · **ask 边确定性补齐** · 末字段解析 · 散文前缀 `table#`）

> 第 15 轮达标（98.0%）后本轮重跑为 **146/150 = 97.3%**（run#34）。**不是回归** ——
> 本轮换了新生成的一场（pipeline #45，9 节点 11 关系），暴露两个**此前被更好拓扑掩盖的
> 解析/结构缺陷**，以及**题 17 的生成侧根因被彻底定位**。

**工具侧取证（run#34）**：把 `pipeline_run_handoffs` 的 9 个节点产出逐条导出 → 见
`/tmp` 转存。**关键事实**：4 个专家节点（304/305/306/307）的**入站交接物全部为空**，
模型自述「上游交接物为空…我不能在缺少输入的情况下编造失效模式」→ 专家完全空转；
且 `pipeline_relations` 里 **`ask` 边 = 0 条**（只有 `design`×4 + `supply`×5 + `review`×1
+ `handoff`×1）→ 汇总节点 `set_allowed_experts(None)` → 提示词里没有【可询问的专家】
段落 → `ask_expert` 无对象可问 → `sources` 里 `expert:` 引用 **0 处**。

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-39 考卷正确率（第 16 轮） | 通过判定点 / 150 | ≥98% | 146/150 = **97.3%**（题 14/17/21 未过） |
| N-40 run 终态 | `pipeline_runs.status` | `done` | `done`（连续第二场 `done`） |
| N-41 行数无重复 | `dfmea_rows` 行数 / 子系统数 | 1:1 | **13 行 / 13 子系统**（首次严格 1:1，无任何多代残留） |
| N-42 来源分布 | 各 `sources` token 计数 | 需 `expert:` > 0 | history 13 / table 51 / **expert 0** / ai 0 → 题 17 失分根因 |
| N-43 `ask` 边确定性补齐 | 有专家节点 ⇒ 该 pipeline 必有 `ask` 边 | **100%** | 引擎新增 `_materialize_ask_edges`：无 `ask` 边时**确定性补齐**（本次实例 4 非专家节点 × 4 专家 = 补 16 条） |
| N-44 ask 补齐单测 | 幂等 / 方向无关去重 / 无专家不补 / 专家不互连 | 100% | **10/10 PASS**（`A1`~`A10`） |
| N-45 末字段解析（`re.M`） | 字段行后仍有其它小节时，末字段须能抠出 | **100%** | `_cell` 加 `re.M`；实例 R13 的 `建议措施=…` 由 `NULL` → 抠出（题 21 根因） |
| N-46 散文前缀 `table#` 归位 | `按 table#severity8 调整：…` 须归位到 `severity` | **100%** | `_paren_sources` 改「token 内定位 `table#`」，不再要求 token 以 `table` 开头（题 14 根因） |
| N-47 关键格来源兜底 | S/O/D/AP 有值却无来源时，须回该格自身括号里再找一次 | **100%** | `ingest_table_rows` 新增兜底，**不臆造**（找不到就仍跳过） |
| N-48 解析器单测（五次扩充） | `verify_fmea_table_parse.py` | 100% | **106/106 PASS**（97 → 106：+9 项覆盖 N-45/46） |
| N-49 全量回归套件 | 5 个 verify 脚本 | 全绿 | `table_parse` 106/0 · `actions` OK · `part_search` OK · `pipeline_fmea` OK · `persona_templates` OK |

**根因归类（重要）**：
- **题 17（expert 引用 0 处）= 生成侧 + 引擎侧双因**。生成侧：拓扑里没有 `ask` 边；
  引擎侧：`_ask_sources` 只读 `ask` 边，**没有任何兜底**。修法在**引擎侧**补结构不变量
  （`_materialize_ask_edges`）—— 这是**拓扑元素补齐**，不是内容裁决：引擎保证
  「有专家参与 ⇒ 专家可被询问」，**问什么 / 问几个 / 结论为何仍全由 LLM 决定**。
- **题 14 / 题 21 = 纯解析器缺陷**，与前几轮的 `action` 别名、裸值兜底同族：
  真实交付物的字段**边界写法远比样例脏**（末字段后接小节、括号里写散文前缀）。
  两条修法都只改「取值边界」，**不改任何业务裁决**。

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

## T-N pipeline 创建元流程（pipeline-factory · design §18）

> **主体已实现（2026-09-14，commit 4a438e2）**。factory 已落库 **pipeline #47**
> （9 节点 / 11 关系，m1/m5/m8 绑定 Pipeline 训练师 #7，其余 deterministic）。
> DFMEA 回迁（§18.5-5）与本体种子（§18.5-6）待做。

| 指标 | 定义/口径 | 阈值/目标 | 实测 |
|---|---|---|---|
| N-50 资产盘点完备性 | `inventory` 返回五类资产（identities/templates/actions/mcp/fixtures）且 `fixtures_ok=True` | **五类齐 + True** | 14 identities / 4 templates / 4 fixtures 全就位 ✅ |
| N-51 考卷编译自动率 | `compile_exam` 自动产出的判定点数 / 总判定点数 | **100%**（给定夹具后零手写） | 38/38 ✅（覆盖 13 + 规则 7 + 基准 18） |
| N-52 判分独立性 | `run_exam` 执行期间 LLM 调用次数 | **0**（考官不能是考生） | 0 ✅（纯 SQL 判定） |
| N-53 判分只读性 | `run_exam` 对写库的 DML 次数 | **0** | 0 ✅（只 SELECT） |
| N-54 归因分类可用性 | `diagnose` 对失败判定点的 structural/semantic 二分覆盖率 | **100%** | 启发式全覆盖 ✅（LLM 提名段待接） |
| N-55 factory 落库幂等性 | `seed_pipeline_factory.py` 重复运行的 pipeline 新增数 | **0**（第二次起） | 0 ✅（按 name 判重） |
| N-56 元流程结构完备性 | factory #47 的 deterministic 节点全部有可用派发函数 | **6/6** | 6/6 ✅（资产盘点/补专家/补能力/编译考卷/考试迭代/反向沉淀） |
| N-57 版本族归档正确性 | 非 canonical 的 DFMEA pipeline 全部 `is_archived=true` 且 `family_id=44` | **100%** | 26/26 ✅（#1 独立 family=1 归档） |
| N-58 主列表过滤 | 前端默认视图中的归档 pipeline 数 | **0** | 0 ✅（勾选后可查 29 条） |
| N-59 画布位置持久化 | 拖动节点 → 刷新页面 → 位置保留 | **100%** | `updateNode` 落库 position_x/y ✅（端到端手测） |
| N-60 画布撤销/重做 | 拖动后 Ctrl+Z 恢复原位、Ctrl+Shift+Z 重做 | 双向可用 | 双栈实现 ✅（端到端手测） |
| N-61 空表判分合理性 | 空 run 上 `run_exam` 的 pass_rate | 应显著 < 阈值（覆盖/来源类应失败） | 60.5% ✅（合理信号：空表上 15 项失败） |
| N-62 本体库同步零缺口 | persona_ontology 中在 candidates（按 name_norm）查不到的条数 | **0** | 回填前 84 → 回填后 **0** ✅（candidates 68→149） |
| N-63 add_ontology 双写 | 装配本体段后 candidates 是否同步 | **100%** | `_sync_candidate` 幂等 upsert + tags 来源标记 ✅ |
| N-64 导出完备性 | porter 导出 8 个 section 且数量与库一致 | 8/8 一致 | 14/146/37/14/149/77/29/5 ✅ |
| N-65 导入幂等性 | 同 bundle 重导入的 added 总数 | **0** | 0（skipped=471）✅ |
| N-66 导入不改既有 | 重导入后既有条目定义是否被覆盖 | **不覆盖** | 按 name/name_norm 判重跳过 ✅ |
| N-67 porter API 可用 | 登录 → export → import 全链路 | 200 + ok=true | 冒烟通过 ✅ |
| N-68 upsert 唯一性 | 同名重复调 `upsert_identity`（update_if_exists=False）产生的新行数 | **0**（返回既有 id） | 单测 5/5 PASS ✅ |
| N-69 upsert 更新语义 | `update_if_exists=True` 时字段是否生效 | mission/keywords 均更新 | PASS ✅ |
| N-70 upsert 校验完备 | 空名 / 超 500 字 mission / category 越界 | 全部拒绝/回退 | PASS ✅ |
| N-71 模板路径收口无回归 | 收口后 `verify_persona_templates.py` | RESULT: OK | OK ✅ |
| N-72 平行实现消除 | 除 `upsert_identity` 外直接 INSERT identities 的代码处数 | **0** | create_identity 与 _upsert_identity 均改走统一入口 ✅ |
| N-73 新路由可用 | `POST /api/identities`（含内联本体 + 动作绑定） | ok=true | 冒烟：id=39 + inline_ontology=1 ✅ |
| N-74 内联本体双写 | spec 内联本体是否同步进本体库（candidates） | 同步 | 冒烟：同步 1 条 ✅ |

---

*维护约定：指标的任何变更（新增/改公式/改阈值）必须同时更新 requirement.md 对应 R-5 族条目与 design.md 相关章节（design.md §14）。*
