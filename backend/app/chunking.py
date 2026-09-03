# -*- coding: utf-8 -*-
"""Chunking: sentence-boundary split with overlap, hard-split fallback.

- sentence boundaries: chinese/english punctuation
- a single sentence longer than chunk_size is hard-split with overlap
  (real PDFs often have no punctuation - must not become one giant chunk)
- every chunk records {index, text, start, end} where
  text[start:end] == text slice, so ontology evidence spans map back literally
"""
import re

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 100


def chunk_text(text: str, chunk_size: int | None = None,
               overlap: int | None = None) -> list[dict]:
    chunk_size = chunk_size or DEFAULT_CHUNK_SIZE
    overlap = DEFAULT_OVERLAP if overlap is None else overlap
    if not text or not text.strip():
        return []

    sentences = re.split(r'(?<=[。！？!?；;])', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # hard-split overlong sentences (step keeps overlap between pieces)
    step = max(1, chunk_size - overlap) if overlap > 0 else chunk_size
    pieces: list[str] = []
    for s in sentences:
        if len(s) > chunk_size:
            j = 0
            while j < len(s):
                pieces.append(s[j:j + chunk_size])
                j += step
        else:
            pieces.append(s)

    chunks: list[str] = []
    current = ""
    for s in pieces:
        if current and len(current) + len(s) > chunk_size:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = (tail + s) if tail else s
        else:
            current = (current + s) if current else s
    if current.strip():
        chunks.append(current)

    # position mapping: chunk i starts `overlap` chars before chunk i-1 ends
    # (the tail), clamped to the previous chunk's own start.
    result = []
    prev_start = 0
    cursor = 0
    for i, c in enumerate(chunks):
        start = max(prev_start, cursor - overlap) if i > 0 else 0
        if text[start:start + len(c)] != c:  # safety fallback
            idx = text.find(c, prev_start)
            start = idx if idx != -1 else cursor
        result.append({"index": i, "text": c, "start": start, "end": start + len(c)})
        prev_start = start
        cursor = start + len(c)
    return result
