# -*- coding: utf-8 -*-
"""Embedding：文本转向量。

正式模式：OpenAI 兼容 embedding 接口（DeepSeek/GLM 均可）。
本地模式：确定性 hash embedding（无外部依赖，可复现，够演示用）。

hash embedding 思路：把文本按字符 n-gram 哈希到固定维度的桶里，
再 L2 归一化。它不是语义向量，但能让「相同关键词的文本」在向量空间
里更接近，足以演示「检索命中」的链路。
"""
import hashlib
import math
import config


def _hash_embed(text: str, dim: int = None) -> list[float]:
    dim = dim or config.EMBED_DIM
    vec = [0.0] * dim
    if not text:
        return vec

    # 字符 n-gram（1~3），每个 n-gram 哈希到 dim 个桶
    for n in (1, 2, 3):
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            vec[h % dim] += 1.0

    # L2 归一化
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _openai_embed(text: str) -> list[float]:
    """OpenAI 兼容 embedding 接口。"""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("正式模式需要 openai 库：pip install openai")

    client = OpenAI(base_url=config.EMBED_BASE_URL, api_key=config.EMBED_API_KEY)
    resp = client.embeddings.create(model=config.EMBED_MODEL, input=[text])
    return resp.data[0].embedding


def embed(text: str) -> list[float]:
    """统一入口：有 key 走外部 embedding，无 key 走本地 hash。"""
    if config.EMBED_API_KEY:
        try:
            return _openai_embed(text)
        except Exception:
            # 外部 embedding 失败时降级，保证链路不断
            pass
    return _hash_embed(text)


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量已归一化时就是点积，这里仍做安全归一化）。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
