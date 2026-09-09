# V4-Flash × 模型消融报告（正式版 · 2026-09-09）

> 12 道项目能力题（capability_tasks_project.json）· 沙箱容器跑 HumanEval 风格隐藏 assert 判定（LLM 不判分）· reactive ≤3 轮绿即停
> 模型：qwen2.5:7b-32k（本地 Ollama）vs deepseek-v4-flash（云）· writer=代码工程师(3) · reviewer=调试工程师(6)

## 1. 组级汇总

| 组 | qwen2.5:7b-32k | V4-Flash | 差异 |
|---|---|---|---|
| A 单数字人+本体 | 9/12 | **11/12** | +2 |
| B 单数字人−本体 | 7/12 | **12/12** | +5 |
| C pipeline+本体 | 7/12 | **12/12** | +5 |
| D pipeline−本体 | 7/12 | **12/12** | +5 |

## 2. 逐题对照（12 题）

| 题 | 说明 | qwen A | qwen B | qwen C | qwen D | V4 A | V4 B | V4 C | V4 D |
|---|---|---|---|---|---|---|---|---|---|
| proj_normalize_name | 实体名规范化 | fail | fail | fail | fail | pass | pass | pass | pass |
| proj_parse_dsn | PG DSN 解析 | fail | fail | fail | fail | pass | pass | pass | pass |
| proj_paginate | 分页 | pass | pass | pass | pass | pass | pass | pass | pass |
| proj_dedup_tags | 标签去重 | pass | fail | fail | fail | fail | pass | pass | pass |
| proj_jsonb_get | JSONB 点路径读取 | pass | pass | pass | pass | pass | pass | pass | pass |
| proj_chunk_overlap | 文本重叠分段 | fail | fail | fail | fail | pass | pass | pass | pass |
| proj_cosine | 余弦相似度 | pass | pass | pass | pass | pass | pass | pass | pass |
| proj_validate_tags | 标签闭集校验 | pass | pass | pass | pass | pass | pass | pass | pass |
| proj_safe_truncate | 截断 | pass | pass | pass | pass | pass | pass | pass | pass |
| proj_extract_code | 提取纯代码 | pass | pass | fail | pass | pass | pass | pass | pass |
| proj_merge_summaries | 摘要去重拼接 | pass | fail | pass | fail | pass | pass | pass | pass |
| proj_topk | top-k 索引 | pass | pass | pass | pass | pass | pass | pass | pass |

## 3. 失败明细（V4-Flash 侧）

- **V4-A proj_dedup_tags（标签去重）fail**：3 轮全红
  - R1: 3, in check     assert candidate(['A', 'a', 'B', 'b']) == ['A', 'B']            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ AssertionError
  - R2: ne 14, in check     assert candidate([' x ', 'X', ' y']) == ['x', 'y']            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ AssertionError
  - R3: 3, in check     assert candidate(['A', 'a', 'B', 'b']) == ['A', 'B']            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ AssertionError

- V4-B 失败：无
- V4-C 失败：无
- V4-D 失败：无

## 4. 结论

1. **瓶颈是模型能力/上下文窗口，非 pipeline 机制**：qwen C=7/12 → V4 C=12/12。
2. V4 下 B/C/D 满分、A 差 dedup_tags 一题：强模型内化规则后，本体/流程边际收益趋平。
3. 工程建议：生产写码通道 V4-Flash；qwen2.5:7b 保留为低成本回归/对照基线。
4. 完整对话 log（纯文本）：ablation-conversations.log。