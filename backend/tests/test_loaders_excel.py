# -*- coding: utf-8 -*-
"""Pure-function tests for loaders (Excel / CSV chunking + source_meta).

These need NO PostgreSQL — they exercise `loaders.load_text` against real
.xlsx/.csv files written to tmp_path. They pin the chunking contract:

  - xlsx: one chunk per sheet x row-window (80 rows); every chunk carries
    source_meta = {file, file_type, sheet, header, start_row, end_row}
  - xlsx without header row: header_for_meta falls back to col1..colN and
    rows are NOT offset by the header line
  - csv: single sheet "-", header + row range in source_meta
  - a broken / empty file must never raise: it returns note=... chunks=[]
"""
from pathlib import Path

import pytest

from app import loaders


def _write_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    """sheets = {sheet_name: [ [c1,c2,...], ... ]} — first list = row 0."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def test_excel_single_sheet_header_and_rows(tmp_path):
    """Header row is not data; source_meta points at the real data rows."""
    p = tmp_path / "t.xlsx"
    _write_xlsx(p, {"报销": [
        ["费用类型", "金额", "部门"],
        ["差旅", 1200.5, "市场部"],
        ["办公", 300, "行政部"],
    ]})
    doc = loaders.load_text(p)
    assert doc["file_type"] == "xlsx"
    assert doc["note"] is None
    assert len(doc["chunks"]) == 1

    c = doc["chunks"][0]
    assert c["index"] == 0
    assert "费用类型" in c["text"] and "差旅" in c["text"]
    assert "1200.5" in c["text"]
    m = c["source_meta"]
    assert m["file"] == "t.xlsx"
    assert m["file_type"] == "xlsx"
    assert m["sheet"] == "报销"
    assert m["header"] == ["费用类型", "金额", "部门"]
    # data starts at row 2 (header row 1 + data offset)
    assert m["start_row"] == 2
    assert m["end_row"] == 3
    # whole-doc text keeps the sheet anchor
    assert "## sheet: 报销" in doc["text"]


def test_excel_without_header_falls_back_to_col_labels(tmp_path):
    """A sheet whose first row is all-empty is data, not a header."""
    p = tmp_path / "nohead.xlsx"
    _write_xlsx(p, {"S1": [
        ["", ""],          # all-empty first row -> has_header=False
        ["a", "b"],
        ["c", "d"],
    ]})
    doc = loaders.load_text(p)
    assert doc["note"] is None
    c = doc["chunks"][0]
    m = c["source_meta"]
    assert m["header"] == ["col1", "col2"]
    # no header offset: the empty first row counts as data row 1
    assert m["start_row"] == 1
    assert m["end_row"] == 3


def test_excel_wide_sheet_splits_into_80_row_windows(tmp_path):
    """A >80-row sheet must split into consecutive windows with absolute rows."""
    p = tmp_path / "wide.xlsx"
    rows = [["id", "v"]]
    rows += [[i, f"v{i}"] for i in range(1, 170)]  # 169 data rows
    _write_xlsx(p, {"big": rows})

    doc = loaders.load_text(p)
    chunks = doc["chunks"]
    assert len(chunks) == 3  # 169 data rows = 80 + 80 + 9

    # window 1: rows 2..81 ; window 2: 82..161 ; window 3: 162..170
    assert chunks[0]["source_meta"]["start_row"] == 2
    assert chunks[0]["source_meta"]["end_row"] == 81
    assert chunks[1]["source_meta"]["start_row"] == 82
    assert chunks[1]["source_meta"]["end_row"] == 161
    assert chunks[2]["source_meta"]["start_row"] == 162
    assert chunks[2]["source_meta"]["end_row"] == 170

    # provenance preserved per chunk, contiguous row coverage, no overlap
    spans = [(c["source_meta"]["start_row"], c["source_meta"]["end_row"])
             for c in chunks]
    assert spans[0][1] + 1 == spans[1][0]
    assert spans[1][1] + 1 == spans[2][0]
    assert all(c["source_meta"]["sheet"] == "big" for c in chunks)
    assert all(c["source_meta"]["header"] == ["id", "v"] for c in chunks)
    # every window keeps its own text so a retrieval hit can be traced to rows
    assert "v80" in chunks[0]["text"]
    assert "v81" in chunks[1]["text"] or "v82" in chunks[1]["text"]
    assert "v169" in chunks[2]["text"]


def test_excel_multiple_sheets_all_become_chunks(tmp_path):
    p = tmp_path / "multi.xlsx"
    _write_xlsx(p, {
        "一月": [["a"], ["1"]],
        "二月": [["b"], ["2"]],
    })
    doc = loaders.load_text(p)
    assert len(doc["chunks"]) == 2
    sheets = [c["source_meta"]["sheet"] for c in doc["chunks"]]
    assert sheets == ["一月", "二月"]


def test_excel_corrupt_file_returns_note_not_raise(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"this is not a real xlsx file")
    doc = loaders.load_text(p)  # must not raise
    assert doc["file_type"] == "xlsx"
    assert doc["chunks"] == []
    assert "error" in (doc["note"] or "")


def test_excel_header_only_no_data_rows(tmp_path):
    """A sheet with a header but zero data rows produces no chunks."""
    p = tmp_path / "headonly.xlsx"
    _write_xlsx(p, {"S1": [
        ["费用类型", "金额"],
    ]})
    doc = loaders.load_text(p)
    assert doc["chunks"] == []
    assert "no rows" in (doc["note"] or "")


def test_csv_single_sheet_dash_header_and_row_range(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        "城市,销量\n上海,100\n北京,200\n", encoding="utf-8")
    doc = loaders.load_text(p)
    assert doc["file_type"] == "csv"
    assert doc["note"] is None
    assert len(doc["chunks"]) == 1
    c = doc["chunks"][0]
    assert "城市: 上海" in c["text"] or "城市: 上海 | 销量: 100" in c["text"]
    m = c["source_meta"]
    assert m["sheet"] == "-"
    assert m["header"] == ["城市", "销量"]
    # csv numbering: header row = 1, data rows start at 2 (matches xlsx)
    assert m["start_row"] == 2
    assert m["end_row"] == 3
    assert m["file"] == "t.csv"


def test_txt_source_meta_has_no_sheet_rows(tmp_path):
    """txt/md chunks keep char offsets, not sheet/row provenance."""
    p = tmp_path / "a.md"
    p.write_text("# 标题\n\n正文内容。", encoding="utf-8")
    doc = loaders.load_text(p)
    assert doc["file_type"] == "md"
    assert len(doc["chunks"]) == 1
    m = doc["chunks"][0]["source_meta"]
    assert m["file"] == "a.md"
    assert m["file_type"] == "md"
    assert m.get("sheet") is None
    assert m.get("start_row") is None
    assert m.get("end_row") is None
    assert "start_char" in m and "end_char" in m
