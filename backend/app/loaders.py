# -*- coding: utf-8 -*-
"""Loaders: markdown + pdf + text + excel + csv + docx -> plain text + chunks.

Each loader returns the same shape:

  {
    "name": <basename>,
    "path": <absolute path>,
    "text": <concatenated text (for content-hash dedupe)>,
    "note": <None | str> ,
    "file_type": <"md" | "pdf" | "txt" | "xlsx" | "csv" | "docx">,
    "chunks": [
      {"index": int, "text": str, "source_meta": {...}},
      ...
    ],
  }

`chunks` carries the FINAL chunk list that `ingest.py` will persist to the
`chunks` table. For md/pdf/txt/docx the list is a single whole-text chunk
today (so the original chunking.chunk_text slice split still works); for
tabular formats (xlsx/csv) each spreadsheet region (sheet + header + row
range) becomes its own chunk, so the chunk->source provenance can be
rendered back to the user as "which sheet/rows".
"""
from pathlib import Path
from typing import Iterable

import pypdf


# ---- supported extensions & file_type mapping --------------------------------

SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".xlsx", ".xls",
                  ".csv", ".docx"}


def _ext_of(p: Path) -> str:
    return p.suffix.lower()


def _file_type_of(ext: str) -> str:
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".xlsx", ".xls"):
        return "xlsx"
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".txt":
        return "txt"
    return ext.lstrip(".")


# ---- public API --------------------------------------------------------------

def load_text(path: str | Path) -> dict:
    """Load a supported document. Returns the unified dict (see module header)."""
    p = Path(path)
    ext = _ext_of(p)
    if ext in (".md", ".markdown", ".txt"):
        return _load_text_plain(p)
    if ext == ".pdf":
        return _load_pdf(p)
    if ext in (".xlsx", ".xls"):
        return _load_excel(p)
    if ext == ".csv":
        return _load_csv(p)
    if ext == ".docx":
        return _load_docx(p)
    raise ValueError(f"unsupported file type: {p.name}")


def scan_workdir(work_dir: str, recursive: bool = True) -> list[Path]:
    """Find supported documents under work_dir."""
    if not work_dir:
        return []
    root = Path(work_dir)
    if not root.exists() or not root.is_dir():
        return []
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and _ext_of(p) in SUPPORTED_EXTS)


# ---- plain text / markdown ---------------------------------------------------

def _load_text_plain(p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\x00", "")  # PG text can't hold NUL
    file_type = _file_type_of(_ext_of(p))
    chunks = [_make_chunk(0, text, file_type, p.name, start=0, end=len(text))]
    return {"name": p.name, "path": str(p), "text": text, "note": None,
            "file_type": file_type, "chunks": chunks}


# ---- PDF ---------------------------------------------------------------------

def _load_pdf(p: Path) -> dict:
    reader = pypdf.PdfReader(str(p))
    parts = []
    empty_pages = 0
    for page in reader.pages:
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(t)
        else:
            empty_pages += 1
    text = "\n".join(parts)
    text = text.replace("\x00", "")  # PG text can't hold NUL; some PDFs emit it
    note = None
    if not text.strip():
        note = "no text layer (scanned pdf?) - skipped"
    elif empty_pages > len(parts):
        note = f"{empty_pages} pages without text layer were dropped"
    chunks = [_make_chunk(0, text, "pdf", p.name, start=0, end=len(text))]
    return {"name": p.name, "path": str(p), "text": text, "note": note,
            "file_type": "pdf", "chunks": chunks}


# ---- CSV ---------------------------------------------------------------------

def _load_csv(p: Path) -> dict:
    """Read a UTF-8 CSV. Single sheet, header + row range becomes the chunk.

    Falls back to a 'no header detected' representation if the first row has
    empty cells, so we never lose the file."""
    import csv as _csv
    try:
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(_csv.reader(f))
    except Exception as e:
        return {"name": p.name, "path": str(p), "text": "",
                "note": f"csv read error: {e}", "file_type": "csv",
                "chunks": []}
    if not rows:
        return {"name": p.name, "path": str(p), "text": "",
                "note": "csv is empty", "file_type": "csv", "chunks": []}
    header = rows[0]
    data_rows = rows[1:]
    text = "\n".join(_format_rows([header] + data_rows))
    chunks = [_make_chunk(0, text, "csv", p.name, sheet="-", header=header,
                          start_row=2, end_row=len(rows))]
    return {"name": p.name, "path": str(p), "text": text, "note": None,
            "file_type": "csv", "chunks": chunks}


# ---- Word (.docx) ------------------------------------------------------------

def _load_docx(p: Path) -> dict:
    """Extract text from a .docx: paragraphs AND tables, kept in document
    order (tables stay at their position among paragraphs). Table rows are
    rendered as 'cell | cell' lines so tabular content survives."""

    def _para_text(p_el, doc):
        from docx.text.paragraph import Paragraph
        return Paragraph(p_el, doc).text

    def _table_lines(t_el, doc):
        from docx.table import Table
        tbl = Table(t_el, doc)
        out = []
        for row in tbl.rows:
            cells = []
            for c in row.cells:
                t = (c.text or "").strip().replace("\n", " ")
                if t and (not cells or cells[-1] != t):
                    cells.append(t)  # dedupe merged-cell repeats
            if cells:
                out.append(" | ".join(cells))
        return out

    try:
        from docx import Document
        from docx.oxml.ns import qn
        doc = Document(str(p))
    except Exception as e:
        return {"name": p.name, "path": str(p), "text": "",
                "note": f"docx open error: {e}", "file_type": "docx",
                "chunks": []}

    parts: list[str] = []
    for child in doc.element.body.iterchildren():
        try:
            if child.tag == qn("w:p"):
                t = _para_text(child, doc).strip()
                if t:
                    parts.append(t)
            elif child.tag == qn("w:tbl"):
                parts.extend(_table_lines(child, doc))
        except Exception:
            continue  # one bad block must not kill the whole document

    text = "\n".join(parts)
    note = None
    if not text.strip():
        note = "docx has no extractable text (empty or image-only?)"
    chunks = [_make_chunk(0, text, "docx", p.name, start=0, end=len(text))]
    return {"name": p.name, "path": str(p), "text": text, "note": note,
            "file_type": "docx", "chunks": chunks}


# ---- Excel (xlsx/xls) --------------------------------------------------------

# `text` for the whole doc is the concatenation of every sheet (kept for the
# content-hash dedupe in ingest_workdir). `chunks` is one entry per sheet+row
# range so the provenance is preserved.
EXCEL_CHUNK_MAX_ROWS = 80   # split huge sheets into N-row windows


def _excel_sheet_rows(p: Path) -> list[tuple[str, list[list]]]:
    """[(sheet_name, rows)] for an Excel file.

    .xlsx goes through openpyxl; legacy binary .xls (BIFF) is not supported
    by openpyxl and must use xlrd 2.x. Cell values are normalized to the same
    python types (str / int / float / bool / None / ISO date str) so the two
    sources share one chunking pipeline."""
    if _ext_of(p) == ".xls":
        import xlrd
        from xlrd import xldate_as_datetime
        book = xlrd.open_workbook(str(p))
        out: list[tuple[str, list[list]]] = []
        for sh in book.sheets():
            rows: list[list] = []
            for rx in range(sh.nrows):
                row: list = []
                for cx in range(sh.ncols):
                    cell = sh.cell(rx, cx)
                    ctype, val = cell.ctype, cell.value
                    if ctype == xlrd.XL_CELL_EMPTY:
                        v = None
                    elif ctype == xlrd.XL_CELL_TEXT:
                        v = val
                    elif ctype == xlrd.XL_CELL_NUMBER:
                        v = int(val) if float(val).is_integer() else val
                    elif ctype == xlrd.XL_CELL_DATE:
                        try:
                            v = xldate_as_datetime(val, book.datemode).isoformat()
                        except Exception:
                            v = val
                    elif ctype == xlrd.XL_CELL_BOOLEAN:
                        v = bool(val)
                    else:  # XL_CELL_ERROR / unknown -> keep text form
                        v = str(val)
                    row.append(v)
                rows.append(row)
            if rows:
                out.append((sh.name, rows))
        return out

    # .xlsx (openpyxl)
    from openpyxl import load_workbook
    wb = load_workbook(filename=str(p), read_only=True, data_only=True)
    out = []
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = [list(r) if r is not None else []
                    for r in ws.iter_rows(values_only=True)]
            if rows:
                out.append((sheet_name, rows))
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return out


def _load_excel(p: Path) -> dict:
    try:
        sheets = _excel_sheet_rows(p)
    except Exception as e:
        return {"name": p.name, "path": str(p), "text": "",
                "note": f"excel open error: {e}", "file_type": "xlsx",
                "chunks": []}

    all_text_parts: list[str] = []
    chunks: list[dict] = []
    idx = 0

    try:
        for sheet_name, rows in sheets:
            if not rows:
                continue
            # decide header: if row 0 has any non-empty cell, use it as the
            # column header line; otherwise no header (label rows numerically).
            header = [str(c).strip() if c is not None else ""
                      for c in rows[0]]
            has_header = any((c or "").strip() for c in rows[0])
            header_for_meta = header if has_header else \
                [f"col{i+1}" for i in range(max((len(r) for r in rows), default=0))]
            data_rows = rows[1:] if has_header else rows

            # chunk by row windows (keeps provenance = which rows)
            for win_start in range(0, len(data_rows), EXCEL_CHUNK_MAX_ROWS):
                win_rows = data_rows[win_start: win_start + EXCEL_CHUNK_MAX_ROWS]
                if not win_rows:
                    continue
                start_row_abs = (win_start + 2) if has_header \
                    else (win_start + 1)
                end_row_abs = start_row_abs + len(win_rows) - 1
                lines = _format_rows([header] + win_rows) if has_header \
                    else _format_rows(win_rows, prefix_col=header_for_meta)
                chunk_text = "\n".join(lines)
                all_text_parts.append(
                    f"## sheet: {sheet_name} (rows {start_row_abs}-{end_row_abs})\n"
                    + chunk_text)
                chunks.append({
                    "index": idx,
                    "text": chunk_text,
                    "source_meta": {
                        "file": p.name,
                        "file_type": "xlsx",
                        "sheet": sheet_name,
                        "header": header_for_meta,
                        "start_row": start_row_abs,
                        "end_row": end_row_abs,
                    },
                })
                idx += 1
    finally:
        pass

    if not chunks:
        return {"name": p.name, "path": str(p), "text": "",
                "note": "excel has no rows", "file_type": "xlsx",
                "chunks": []}
    return {"name": p.name, "path": str(p),
            "text": "\n\n".join(all_text_parts), "note": None,
            "file_type": "xlsx", "chunks": chunks}


def _format_rows(rows: Iterable[list], prefix_col: list[str] | None = None) \
        -> list[str]:
    """Render rows as 'col: value' lines (one row per line, columns |-separated)."""
    out = []
    rows = list(rows)
    if not rows:
        return out
    header = prefix_col if prefix_col else [
        (str(c).strip() if c else "") for c in rows[0]]
    data = rows[1:] if prefix_col is None else rows
    for r in data:
        cells = [("" if v is None else str(v)) for v in r]
        # align length with header (pad with empty if data row is short)
        parts = []
        for i, val in enumerate(cells):
            label = header[i] if i < len(header) else f"col{i+1}"
            parts.append(f"{label}: {val}")
        out.append(" | ".join(parts))
    return out


# ---- chunk helper ------------------------------------------------------------

def _make_chunk(index: int, text: str, file_type: str, name: str,
                *, start: int | None = None, end: int | None = None,
                sheet: str | None = None, header: list[str] | None = None,
                start_row: int | None = None, end_row: int | None = None) \
        -> dict:
    meta: dict = {"file": name, "file_type": file_type}
    if start is not None:
        meta["start_char"] = start
    if end is not None:
        meta["end_char"] = end
    if sheet is not None:
        meta["sheet"] = sheet
    if header is not None:
        meta["header"] = header
    if start_row is not None:
        meta["start_row"] = start_row
    if end_row is not None:
        meta["end_row"] = end_row
    return {"index": index, "text": text, "source_meta": meta}
