# -*- coding: utf-8 -*-
"""Loaders: markdown + pdf -> plain text (Windows side reads the files)."""
from pathlib import Path

import pypdf


def load_text(path: str | Path) -> dict:
    """Load a .md/.pdf/.txt file. Returns {name, path, text, note}.

    note: None on success; a reason string when content is unusable
    (e.g. scanned pdf without text layer) - caller marks the file skipped.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(p)
    if suffix in (".md", ".markdown", ".txt"):
        return {"name": p.name, "path": str(p),
                "text": p.read_text(encoding="utf-8", errors="replace"), "note": None}
    raise ValueError(f"unsupported file type: {p.name}")


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
    note = None
    if not text.strip():
        note = "no text layer (scanned pdf?) - skipped"
    elif empty_pages > len(parts):
        note = f"{empty_pages} pages without text layer were dropped"
    return {"name": p.name, "path": str(p), "text": text, "note": note}


def scan_workdir(work_dir: str, recursive: bool = True) -> list[Path]:
    """Find supported documents under work_dir."""
    if not work_dir:
        return []
    root = Path(work_dir)
    if not root.exists() or not root.is_dir():
        return []
    exts = {".md", ".markdown", ".txt", ".pdf"}
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in exts)
