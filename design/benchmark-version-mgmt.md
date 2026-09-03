# 数字人四组对照 Benchmark + 本体问题归因 + 版本管理

设计图：`benchmark-version-mgmt.svg`（2026-09-02 定稿）

## 流程五段

1. **选题（0 LLM）**：`persona_ontology.source_candidate_id → mentions.chunk_id → quiz` 关联，取与该数字人本体强相关的题（默认 15，上限 30，四组共用同一批）。
2. **四组对照答题（本地模型 chat_ollama，低温单次采样）**：
   - G0 `none` 裸模型（无 RAG 无本体）
   - G1 `ontology` 仅本体（动态检索窗口 20% 预算，复用 `chat._retrieve_context`）
   - G2 `rag` 仅 RAG（`ingest.search` top3 chunk 原文）
   - G3 `rag_ontology` 两者叠加
3. **判卷**：GLM（chat2）提名 verdict ∈ {correct, partial, wrong, refused}（四分类闭集）→ 确定性终审（闭集校验 + 拒答词检测，代码可覆盖 LLM）→ 统计 + 结论模板（代码生成，无 LLM）。
   - 准确率 = correct 占比 · 幻觉率 = wrong 占比 · 拒答率 = refused 占比 · partial 单列
   - 边际：本体边际 = G3−G2 · RAG 边际 = G3−G1 · 总增益 = G3−G0
4. **错误归因提名**：对本体组（G1/G3）verdict ∈ {wrong, partial} 的题，GLM 分析提名问题本体 `{name, action ∈ annotate|update|delete, reason, suggested_definition?, note?}` → 确定性闭集校验（name 必须 ∈ 该题注入的本体 ∧ persona_ontology active；update 缺定义降级 annotate；按 name 去重、delete > update > annotate）→ 待审变更集。
5. **版本管理（用户终审）**：卡片上逐条审（忽略无效提名）→ 一键 merge = 先快照当前本体段 → 应用（标注→写 note 注记 · 修改→改 definition · 删除→status=deprecated 软删除）→ 版本 v+1 落库；任意版本可回滚 = 恢复快照（回滚前自动再快照）。

## 新增表（db.py）

```sql
persona_benchmarks(id, identity_id, job_id, model, judge, total, stats JSON, conclusion, status running|done|failed, error, created_at)
persona_benchmark_items(id, benchmark_id, quiz_id, question, answer, arm, reply, verdict, created_at)
persona_ontology_changes(id, identity_id, benchmark_id, ontology_id, name, action annotate|update|delete, suggested_definition, note, reason, evidence JSON, status pending|merged|rejected, version_id, created_at)
persona_ontology_versions(id, identity_id, version, benchmark_id, changelog JSON, snapshot JSON, created_at, UNIQUE(identity_id, version))
-- migration: persona_ontology 加 note 列（annotate 注记）
```

## API

- `POST /api/benchmark/run` `{identity_id, limit?, ollama_model?}` → `{job_id}`（job kind=benchmark，后台跑）
- `GET /api/benchmark?identity_id=` → `{benchmark, changes, versions}`（卡片加载最新结论 + 待审提名 + 版本历史）
- `POST /api/benchmark/changes/{id}/reject` → 忽略一条提名
- `POST /api/benchmark/merge` `{identity_id}` → 快照 + 应用全部 pending + 版本 v+1
- `POST /api/benchmark/rollback` `{identity_id, version_id}` → 恢复该版本快照（回滚前自动快照当前状态为新版本）

## 铁律落位

- GLM 判卷 verdict 与归因提名都只是**提名**：闭集校验 + 拒答词检测 + 归因 name 闭集由确定性代码终审。
- 结论由代码模板生成，无 LLM 参与。
- merge / 忽略 / 回滚全部由**用户**在卡片上终审；LLM 无终审权。
