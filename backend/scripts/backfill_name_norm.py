# -*- coding: utf-8 -*-
"""一次性回填 candidates.name_norm（规范化去重键）。

用法（backend 目录下）：
    ../.venv/Scripts/python.exe -X utf8 scripts/backfill_name_norm.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import db  # noqa: E402
from app.ontology import _norm_name  # noqa: E402


def backfill() -> None:
    conn = db.get_conn()
    rows = conn.execute("SELECT id, name FROM candidates").fetchall()
    for r in rows:
        conn.execute("UPDATE candidates SET name_norm=? WHERE id=?",
                     (_norm_name(r["name"]), r["id"]))
    conn.commit()
    db.reset_conn()
    print(f"回填完成：{len(rows)} 个候选的 name_norm 已写入")


if __name__ == "__main__":
    backfill()
