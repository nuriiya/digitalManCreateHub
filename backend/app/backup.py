# -*- coding: utf-8 -*-
"""数据库全量导出 / 导入（数字人 + 本体 + RAG + pipeline + 能力题/工具）。

导出：把核心业务表打包成 JSON 文件，写入一个「可 git 提交」的目录，
     并生成 .gitattributes 声明大文件走 Git LFS（RAG chunks 原文 + embedding
     可能很大，需要大文件上传）。
导入：从导出目录恢复（清空重建，保留原始 id 以维持外键引用）。

目录结构：
    exports/
        .gitattributes           Git LFS 规则（大文件）
        manifest.json            导出清单（时间 / 表清单 / 行数 / schema 版本）
        identities.json          数字人（六元组主体 + reactive 开关）
        anchors.json             锚点本体
        persona_ontology.json    数字人本体段
        persona_actions.json     数字人动作（含执行类动作）
        candidates.json          本体候选
        relations.json           本体关系
        mentions.json            证据链
        documents.json           RAG 文档（含 embedding → LFS 大文件）
        chunks.json              RAG 分段（原文 + embedding → LFS 大文件）
        pipelines.json           pipeline 主体
        pipeline_nodes.json      节点
        pipeline_relations.json  关系
        pipeline_changes.json    变更提名
        capability_tasks.json    能力题
        capability_tools.json    沉淀工具
        mcp_servers.json         MCP 服务

铁律：导出/导入都是确定性代码（不经过 LLM），embedding 用 ::text 文本化，
导入时 ::vector 还原。导入是「恢复备份」语义：清空重建，保留 id。
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import db

# 项目根下的导出目录（可 git 提交；大文件走 LFS）
EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "exports"

SCHEMA_VERSION = 1

# (表名, vector 列列表, 是否大文件 LFS)
EXPORT_TABLES: list[tuple[str, list[str], bool]] = [
    ("identities", [], False),
    ("anchors", [], False),
    ("persona_ontology", [], False),
    ("persona_actions", [], False),
    ("candidates", [], False),
    ("relations", [], False),
    ("mentions", [], False),
    ("documents", ["embedding"], True),
    ("chunks", ["embedding"], True),
    ("pipelines", [], False),
    ("pipeline_nodes", [], False),
    ("pipeline_relations", [], False),
    ("pipeline_changes", [], False),
    ("capability_tasks", [], False),
    ("capability_tools", [], False),
    ("mcp_servers", [], False),
]

# 导入清空顺序（子表在前，父表在后；TRUNCATE ... CASCADE 一次清完）
IMPORT_TRUNCATE = (
    "identities", "anchors", "persona_ontology", "persona_actions",
    "candidates", "relations", "mentions", "documents", "chunks",
    "pipelines", "pipeline_nodes", "pipeline_relations", "pipeline_changes",
    "capability_tasks", "capability_tools", "mcp_servers",
)

# 导入插入顺序（父表在前，子表在后，满足外键）
IMPORT_ORDER = [
    "identities", "anchors", "persona_ontology", "persona_actions",
    "documents", "chunks", "candidates", "relations", "mentions",
    "pipelines", "pipeline_nodes", "pipeline_relations", "pipeline_changes",
    "capability_tasks", "capability_tools", "mcp_servers",
]

# 导入时需置 NULL 的外键（指向未导出的运行时数据）
NULLIFY = {
    "capability_tools": ["run_id"],
}

# JSONB 列（导入时 dict → json 字符串，psycopg3 才能适配）
JSONB_COLS = {
    "identities": ["keywords"],
    "persona_actions": ["input_schema"],
    "pipeline_changes": ["payload"],
    "capability_tools": ["input_schema"],
    "chunks": ["source_meta"],
}

# text[] 列（list 直接绑定，psycopg3 + register_vector 支持 list → TEXT[]）
ARRAY_COLS = {
    "candidates": ["tags"],
    "chunks": ["tags"],
    "pipelines": ["tags"],
}


def _table_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name=? ORDER BY ordinal_position", (table,)).fetchall()
    return [r["column_name"] for r in rows]


def _export_table(conn, table: str, vector_cols: list[str]) -> list[dict]:
    cols = _table_columns(conn, table)
    sel = [f'"{c}"::text' if c in vector_cols else f'"{c}"' for c in cols]
    rows = conn.execute(f'SELECT {", ".join(sel)} FROM "{table}"').fetchall()
    return [dict(r) for r in rows]


def export_all(conn, target_dir: str | None = None) -> dict:
    """全量导出到导出目录，返回清单。"""
    outdir = Path(target_dir) if target_dir else EXPORT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
    }
    files_written = []

    for table, vector_cols, is_large in EXPORT_TABLES:
        rows = _export_table(conn, table, vector_cols)
        fpath = outdir / f"{table}.json"
        fpath.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        manifest["tables"][table] = {"rows": len(rows), "large": is_large,
                                     "file": f"{table}.json"}
        files_written.append(str(fpath))

    # .gitattributes：大文件走 Git LFS
    large_files = [f"{t}.json" for t, _, is_large in EXPORT_TABLES if is_large]
    attr_lines = [
        "# Git LFS：大文件（RAG 原文 + embedding）走大文件上传，避免 git 仓库膨胀",
        "",
    ]
    for f in large_files:
        attr_lines.append(f"{f} filter=lfs diff=lfs merge=lfs -text")
    (outdir / ".gitattributes").write_text("\n".join(attr_lines) + "\n",
                                            encoding="utf-8")

    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    files_written.append(str(outdir / "manifest.json"))
    files_written.append(str(outdir / ".gitattributes"))

    total = sum(t["rows"] for t in manifest["tables"].values())
    return {"ok": True, "dir": str(outdir), "schema_version": SCHEMA_VERSION,
            "total_rows": total, "tables": manifest["tables"],
            "files": len(files_written),
            "large_lfs": large_files}


def _import_table(conn, table: str, rows: list[dict],
                  vector_cols: list[str]) -> int:
    if not rows:
        return 0
    nullify = NULLIFY.get(table, [])
    cols = list(rows[0].keys())
    ph = []
    for c in cols:
        if c in vector_cols:
            ph.append("?::vector")
        else:
            ph.append("?")
    n = 0
    for row in rows:
        vals = []
        for c in cols:
            v = row.get(c)
            if c in nullify:
                v = None
            # vector 空串 / null → null
            if c in vector_cols and isinstance(v, str) and not v.strip():
                v = None
            # JSONB 列：dict → json 字符串（psycopg3 适配）
            if c in JSONB_COLS.get(table, []) and isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        conn.execute(f'INSERT INTO "{table}" ({", ".join(cols)})'
                     f' VALUES ({", ".join(ph)})', vals)
        n += 1
    return n


def import_all(conn, source_dir: str | None = None) -> dict:
    """从导出目录恢复（清空重建，保留 id）。返回统计。"""
    indir = Path(source_dir) if source_dir else EXPORT_DIR
    manifest_path = indir / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": f"导出目录无 manifest.json：{indir}"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 清空重建（TRUNCATE ... CASCADE 一次清完，含级联的运行时表）
    tables_sql = ", ".join(f'"{t}"' for t in IMPORT_TRUNCATE)
    conn.execute(f"TRUNCATE TABLE {tables_sql} CASCADE")
    conn.commit()

    restored = {}
    for table, vector_cols, _ in EXPORT_TABLES:
        fpath = indir / f"{table}.json"
        if not fpath.exists():
            continue
        rows = json.loads(fpath.read_text(encoding="utf-8"))
        n = _import_table(conn, table, rows, vector_cols)
        restored[table] = n
    conn.commit()

    total = sum(restored.values())
    return {"ok": True, "schema_version": manifest.get("schema_version"),
            "restored": restored, "total_rows": total}


def export_status() -> dict:
    """查看导出目录当前状态（是否存在、有哪些表、大小）。"""
    if not EXPORT_DIR.exists():
        return {"exists": False, "dir": str(EXPORT_DIR)}
    manifest = {}
    mp = EXPORT_DIR / "manifest.json"
    if mp.exists():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    size = sum(f.stat().st_size for f in EXPORT_DIR.rglob("*.json")
               if f.is_file())
    return {"exists": True, "dir": str(EXPORT_DIR),
            "exported_at": manifest.get("exported_at"),
            "schema_version": manifest.get("schema_version"),
            "tables": manifest.get("tables", {}),
            "total_bytes": size}
