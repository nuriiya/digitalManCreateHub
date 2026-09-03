# -*- coding: utf-8 -*-
"""RAG 雏形配置。所有可调项集中在这里。

双模式：
- 本地演示模式（默认）：无 PostgreSQL 时用内存存储 + 本地 hash embedding，
  无需任何外部依赖即可跑通全链路。
- 正式模式：配置 DATABASE_URL 后走 PostgreSQL + pgvector，
  embedding/LLM 走 OpenAI 兼容接口（DeepSeek / GLM 均可）。
"""
import os

# ---- 存储 ----
# 留空 = 本地演示模式（内存存储）；填上 = 正式模式（pgvector）
# 例：postgresql://postgres:postgres@localhost:5432/rag
DATABASE_URL = os.environ.get("RAG_DATABASE_URL", "")

# ---- LLM（OpenAI 兼容）----
LLM_BASE_URL = os.environ.get("RAG_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("RAG_LLM_API_KEY", "")   # 留空 = 降级到规则 summary
LLM_MODEL = os.environ.get("RAG_LLM_MODEL", "deepseek-chat")

# ---- Embedding ----
# OpenAI 兼容 embedding 接口；未配置 key 时用本地 hash embedding 降级
EMBED_BASE_URL = os.environ.get("RAG_EMBED_BASE_URL", LLM_BASE_URL)
EMBED_API_KEY = os.environ.get("RAG_EMBED_API_KEY", LLM_API_KEY)
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "text-embedding-3-small")
# 本地 hash embedding 的维度（无外部 embedding 时生效）
# 接外部 embedding 时请改成对应维度（如 OpenAI 为 1536）
EMBED_DIM = int(os.environ.get("RAG_EMBED_DIM", "384"))

# ---- 分段 ----
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "400"))      # 每段目标字符数
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "40"))  # 段间重叠字符数

# ---- 检索 ----
TOP_K = int(os.environ.get("RAG_TOP_K", "3"))  # 检索返回的 summary 数量
