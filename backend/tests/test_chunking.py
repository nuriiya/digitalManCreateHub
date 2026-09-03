# -*- coding: utf-8 -*-
"""Chunking unit tests: sizes, overlap, position mapping back to source."""


def test_empty_text():
    from app.chunking import chunk_text
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_positions_map_back():
    from app.chunking import chunk_text
    text = "第一句话。第二句话。第三句话内容更长一些。第四句。"
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert len(chunks) >= 2
    for c in chunks:
        # every chunk must be a literal substring at its recorded span
        assert text[c["start"]:c["end"]] == c["text"]


def test_no_sentence_boundary_still_chunks():
    from app.chunking import chunk_text
    text = "很长的没有标点的文本" * 60
    chunks = chunk_text(text, chunk_size=100, overlap=10)
    assert len(chunks) >= 2
    joined = "".join(c["text"] for c in chunks)
    assert len(joined) >= len(text)  # overlap only ever adds text


def test_single_short_sentence():
    from app.chunking import chunk_text
    chunks = chunk_text("只有一句话。")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "只有一句话。"
