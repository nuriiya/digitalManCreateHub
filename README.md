# 数字人雏形 · RAG 提取管线（PostgreSQL + pgvector）

这是数字人雏形的第一步：**从 RAG 提取开始搭建**，实现你定义的
「分段 → 段 summary + 多标签 → 聚合整篇 summary → 检索命中定位原文」链路。

## 核心链路

```
部门文档
  ↓ ① 分段 chunking（带重叠）
每段 → LLM 生成 summary + 打多个标签
  ↓ ② summary 做 embedding（检索入口）
多个段 summary → 聚合出整篇 summary
  ↓ ③ 入库（三层：整篇 summary / 段 summary+标签 / 原文 chunk）
  ↓ ④ 查询：向量命中 summary → 沿 doc_id/seq 回取原文 → 给 LLM 当证据
```

## 双模式

| 模式 | 条件 | 存储 | 向量 | 用途 |
|---|---|---|---|---|
| 本地演示 | 默认（无 PG、无 key） | 内存 | 本地 hash embedding | 立刻跑通全链路 |
| 正式 | 配 `RAG_DATABASE_URL` | PostgreSQL + pgvector | 外部 embedding 接口 | 生产 |

## 快速开始（本地演示，零依赖）

```bash
cd rag_prototype

# 导入文档
python main.py ingest --text "员工报销流程：员工填写报销单，提交部门经理审批，财务审核发票后付款。报销需在30天内提交，超期需说明理由。"

# 查询（命中 summary，回取原文）
python main.py query "报销流程是什么"
```

本地模式无需安装任何第三方库（`openai`/`psycopg` 都不需要），
summary 走规则降级、向量走 hash embedding，链路完整可演示。

## 切到正式模式（pgvector + 真实 LLM）

1. 装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 起数据库：
   ```bash
   docker compose up -d   # PG16 + pgvector
   ```
3. 配置环境变量：
   ```bash
   # Windows PowerShell
   $env:RAG_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/rag"
   $env:RAG_LLM_API_KEY="你的key"
   $env:RAG_LLM_BASE_URL="https://api.deepseek.com/v1"
   $env:RAG_LLM_MODEL="deepseek-chat"
   # 若 embedding 用独立接口，再配 RAG_EMBED_*；否则复用 LLM 配置
   ```
4. 重新跑 ingest / query 即可，存储自动切到 pgvector。

## 关键设计点

- **对 summary 做 embedding，而不是对原文**：查询时先命中摘要（短、语义集中），
  再回取原文，检索更准，也便于打标签分类。
- **三层结构**：整篇 summary（全局概览）/ 段 summary+标签（检索入口）/ 原文 chunk（证据）。
- **证据可引用**：命中 summary 一定带着 `doc_id + seq + 原文`，
  对应数字人「证据关」——LLM 必须有逐字可引的原文，不能凭空写。
- **LLM 无终审权**：summary 和标签都是候选，进本体库前仍要过三关校验 + 分级审批。

## 文件结构

```
rag_prototype/
├── config.py            # 全部可调配置
├── main.py              # CLI 入口（ingest / query）
├── docker-compose.yml   # PG16 + pgvector
├── schema.sql           # 建表脚本
├── requirements.txt     # 正式模式依赖
└── rag/
    ├── chunking.py      # ① 分段
    ├── llm.py           # summary + 标签 + 聚合（LLM/规则降级）
    ├── embedding.py     # 向量（外部接口 / 本地 hash 降级）
    ├── store.py         # 存储（MemoryStore / PGStore 双实现）
    └── pipeline.py      # 提取管线编排 + 检索
```

## 下一步（未实现，设计已定）

- **本体分解器**：在此 RAG 产出的「分段 + summary + 标签」之上，
  做 LLM 本体归纳（无模式抽取 EDC + 基于模式抽取 GraphRAG → 规范化 →
  候选本体提名 → 三关校验 + 分级审批 → ontology.yaml）。
  行业建议见 pipeline.md 第 9 章及对话记录。
