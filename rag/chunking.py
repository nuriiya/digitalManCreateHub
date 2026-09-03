# -*- coding: utf-8 -*-
"""分段（chunking）：把长文档切成带重叠的段落。

重叠保证段落边界处的语义不丢失。每段记录在原文中的起止位置，
供后续「命中 summary → 回取原文」使用。
"""
import config


def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> list[dict]:
    """把文本切成段，返回 [{index, text, start, end}, ...]"""
    chunk_size = chunk_size or config.CHUNK_SIZE
    overlap = overlap or config.CHUNK_OVERLAP
    if not text or not text.strip():
        return []

    # 按句子边界切分（中文标点 + 英文句点），再按长度合并成段
    import re
    sentences = re.split(r'(?<=[。！？!?；;])', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current = ""
    start = 0
    tail = ""  # 上一段末尾的重叠尾巴

    for s in sentences:
        if current and len(current) + len(s) > chunk_size:
            chunks.append(current)
            # 保留尾巴做重叠
            tail = current[-overlap:] if overlap > 0 else ""
            current = tail + s if tail else s
        else:
            current = (current + s) if current else s

    if current.strip():
        chunks.append(current)

    # 计算每段在原文的起止位置
    result = []
    pos = 0
    for i, c in enumerate(chunks):
        idx = text.find(c, pos)
        if idx == -1:
            idx = pos
        result.append({
            "index": i,
            "text": c,
            "start": idx,
            "end": idx + len(c),
        })
        pos = idx + len(c)
    return result
