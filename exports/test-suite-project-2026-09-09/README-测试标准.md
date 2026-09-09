# 项目能力题套件 · 测试标准（Test Spec）

> 时间：2026-09-09 · 套件目录：exports/test-suite-project-2026-09-09/
> 配套：docs/test-metrics.md T-C 能力题指标族；requirement R-5.5~5.7

## 1. 目的

用**以本项目真实模块为蓝本**的能力题，验证「本体注入 → 写码 → 沙箱可执行验证 → 写测改」链路在被测模型上的表现。不同于 HumanEval（通用算法），本套件每题都来自本仓库实际代码/规则，**本体里的规则能真正派上用场**。

## 2. 题目需求标准（出题规范）

每道题 = 一个 Python 函数签名 + docstring（需求）+ 隐藏测试。判定只看函数行为，不看实现。

| # | task | category | 需求（函数） | 对应项目模块 |
|---|---|---|---|---|
| 1 | proj_normalize_name | data_cleaning | 实体名规范化：转小写、去括号内容、去空白 | ontology 去重 name_norm |
| 2 | proj_parse_dsn | db | 解析 PG DSN，端口缺省 5432，密码可空 | data/pg_dsn |
| 3 | proj_paginate | web | 分页计算 offset/has_next，page 从 1 起 | 前端 chunk 分页 |
| 4 | proj_dedup_tags | data_cleaning | 标签去重：忽略大小写与首尾空白，保首见序 | 领域标签 6 类闭集 |
| 5 | proj_jsonb_get | db | 点路径安全读取嵌套 JSON，缺层返回 default | jsonb.maybe_jsonb |
| 6 | proj_chunk_overlap | rag | 文本切重叠分段，段≤max_len，末段不丢 | chunking.py |
| 7 | proj_cosine | vector | 两向量余弦相似度，零向量处理 | embedding 检索 |
| 8 | proj_validate_tags | validation | 标签闭集校验（合法集外返回 False/原因） | 领域标签闭集 |
| 9 | proj_safe_truncate | text | 按字符截断，省略号不计入 max_chars | chat 摘要截断 |
| 10 | proj_extract_code | text | 从回复提取纯代码（``` 块或 def 起始截断） | capability._extract_code |
| 11 | proj_merge_summaries | rag | 多段摘要去重拼接为整篇摘要 | ingest 聚合 |
| 12 | proj_topk | vector | 返回相似度最高的 top-k 索引 | RAG 检索 top_k |

## 3. 模型/数字人通道

- 数字人：代码工程师（identity 3，reactive=True）——**14 条编程规范本体全量注入 system**（写测改循环/输出纯代码/边界条件处理/沙箱边界等）
- 被测模型：`qwen2.5:7b-32k`（本地 Ollama，temperature=0）
- 对照参考（可选）：`llm2`（GLM 5.2）同题同法

## 4. 判定标准（Verifier）

1. 每轮：数字人生成代码 → `_extract_code` 取纯码 → 一次性隔离沙箱（禁网/只读/资源限额）执行隐藏测试
2. `verdict = pass`：隐藏测试全部 assert 通过（含 METADATA 装载）
3. 反应式：fail → 把失败堆栈回灌 → 重新生成，最多 3 轮；**绿即停，不靠 LLM 自评**
4. 套件级指标（见 results/summary.md）：
   - 总通过率 = pass 题数 / 12
   - 首轮通过率 = R1 就 pass 的题数 / 12
   - 平均轮次 = Σ(实际轮数) / 12（1=一轮过）
   - 每轮保留 code + 沙箱输出 → 人工可复核

## 5. 运行方式

```bash
bash run.sh   # 逐题调 /api/capability/run_for_identity → results/<task>.json → summary.md
```

run.sh 需要 backend :8000 可达（dev 容器），内部 curl + jq/python 汇总。

## 6. 结果归属

- 每题详细往返（system 注入、各轮 code、测试输出）：`results/<task_key>.json`
- 汇总表格 + 观察结论：`results/summary.md`
- 判卷由确定性沙箱完成，LLM 仅产生代码（铁律：LLM 无终审权）
