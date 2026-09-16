# -*- coding: utf-8 -*-
"""数字人资产打包导出 / 导入（design §19）。

导出内容（RAG 链路的 documents/chunks/mentions **不含**）：
  - identities        数字人（mission/description/prompt/category/status/reactive）
  - persona_ontology  数字人本体段（按数字人 name 关联）
  - persona_actions   数字人动作（按数字人 name 关联）
  - anchors           数字人锚点（按数字人 name 关联）
  - candidates        本体库（kind/name/definition/status/tags）
  - relations         本体关系（source 候选 name + target_name，扁平结构）
  - pipelines         编排（含节点/关系；persona 绑定按 name 重映射；含归档标志）
  - mcp_servers       MCP 服务

设计口径（与平台铁律一致）：
  - **幂等**：导入按 name/name_norm 判重，已存在则**只补不改**（用户资产不被
    静默覆盖）。
  - **ID 全部按 name 重映射**：导入侧的新 ID 与导出侧无关（跨库迁移安全）。
  - **不携带任何 RAG 数据**：documents/chunks/mentions 留在源库。
"""
import json
import time

from . import db as _db

BUNDLE_VERSION = 1


# ---------------- 导出 ----------------

def export_bundle(conn, include_archived: bool = True) -> dict:
    """把数字人资产打包成可迁移的 JSON dict。"""
    out: dict = {"version": BUNDLE_VERSION, "exported_at": time.time(),
                 "sections": {}}

    out["sections"]["identities"] = [dict(r) for r in conn.execute(
        "SELECT name, mission, description, prompt, category, status, reactive"
        " FROM identities ORDER BY id").fetchall()]

    out["sections"]["persona_ontology"] = [dict(r) for r in conn.execute(
        "SELECT i.name AS identity_name, po.kind, po.name, po.definition,"
        " po.status, po.note"
        " FROM persona_ontology po JOIN identities i ON i.id=po.identity_id"
        " ORDER BY po.id").fetchall()]

    out["sections"]["persona_actions"] = [dict(r) for r in conn.execute(
        "SELECT i.name AS identity_name, pa.name AS action_name, pa.description,"
        " pa.input_schema, pa.kind, pa.builtin_name, pa.status"
        " FROM persona_actions pa JOIN identities i ON i.id=pa.identity_id"
        " ORDER BY pa.id").fetchall()]

    out["sections"]["anchors"] = [dict(r) for r in conn.execute(
        "SELECT i.name AS identity_name, a.name, a.type, a.definition, a.status"
        " FROM anchors a JOIN identities i ON i.id=a.identity_id"
        " ORDER BY a.id").fetchall()]

    out["sections"]["candidates"] = [dict(r) for r in conn.execute(
        "SELECT kind, name, name_norm, definition, status, tags"
        " FROM candidates ORDER BY id").fetchall()]

    # relations 是扁平结构：source_id → candidates，target_name 是文本
    out["sections"]["relations"] = [dict(r) for r in conn.execute(
        "SELECT ca.name AS source_name, r.target_name, r.relation_type"
        " FROM relations r JOIN candidates ca ON ca.id=r.source_id"
        " ORDER BY r.id").fetchall()]

    # pipelines（含节点/关系；persona_id → name）
    where = "" if include_archived else " WHERE is_archived=false"
    pls = [dict(r) for r in conn.execute(
        "SELECT id, name, description, version, status, tags, is_archived"
        " FROM pipelines" + where + " ORDER BY id").fetchall()]
    pipelines = []
    for p in pls:
        pid = p.pop("id")
        nodes = [dict(r) for r in conn.execute(
            "SELECT n.node_key, n.kind, n.step_name, n.position_x, n.position_y,"
            " i.name AS persona_name"
            " FROM pipeline_nodes n LEFT JOIN identities i ON i.id=n.persona_id"
            " WHERE n.pipeline_id=? ORDER BY n.id", (pid,)).fetchall()]
        rels = [dict(r) for r in conn.execute(
            "SELECT fn.node_key AS from_key, tn.node_key AS to_key,"
            " r.relation_type, r.handoff_type, r.handoff_schema"
            " FROM pipeline_relations r"
            " JOIN pipeline_nodes fn ON fn.id=r.from_node_id"
            " JOIN pipeline_nodes tn ON tn.id=r.to_node_id"
            " WHERE r.pipeline_id=? ORDER BY r.id", (pid,)).fetchall()]
        pipelines.append({**p, "nodes": nodes, "relations": rels})
    out["sections"]["pipelines"] = pipelines

    out["sections"]["mcp_servers"] = [dict(r) for r in conn.execute(
        "SELECT name, description, transport, image, command, port, status,"
        " tools, approval_status, source_path"
        " FROM mcp_servers ORDER BY id").fetchall()]

    out["summary"] = {k: len(v) for k, v in out["sections"].items()}
    return out


# ---------------- 导入 ----------------

def import_bundle(conn, data: dict) -> dict:
    """把 export_bundle 产出的 JSON 导入。幂等：按 name 判重，只补不改。

    返回 {stats: {section: {added, skipped}}}。
    """
    from .ontology import _norm_name
    from . import pipeline as P

    stats: dict = {}
    sections = data.get("sections") or {}
    now = _db.now()

    def _stat(sec, added, skipped):
        stats[sec] = {"added": added, "skipped": skipped}

    # ---- 1. identities（先建，后续按 name 关联）----
    added = skipped = 0
    name_to_id: dict = {r["name"]: r["id"] for r in conn.execute(
        "SELECT id, name FROM identities").fetchall()}
    for it in sections.get("identities") or []:
        nm = (it.get("name") or "").strip()
        if not nm or nm in name_to_id:
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT INTO identities(name, mission, description, prompt,"
            " category, status, reactive, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (nm, it.get("mission"), it.get("description"), it.get("prompt"),
             it.get("category") or "domain_expert",
             it.get("status") or "approved",
             bool(it.get("reactive", False)), now))
        conn.commit()
        name_to_id[nm] = cur.lastrowid
        added += 1
    _stat("identities", added, skipped)

    # ---- 2. candidates（本体库，按 name_norm 判重）----
    added = skipped = 0
    norm_to_cid: dict = {}
    for r in conn.execute("SELECT id, name_norm FROM candidates").fetchall():
        if r["name_norm"]:
            norm_to_cid[r["name_norm"]] = r["id"]
    for it in sections.get("candidates") or []:
        nm = (it.get("name") or "").strip()
        norm = _norm_name(nm)
        if not nm or not norm or norm in norm_to_cid:
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT INTO candidates(kind, name, name_norm, definition, status,"
            " tags, created_at) VALUES(?,?,?,?,?,?,?)",
            (it.get("kind") or "概念", nm, norm, it.get("definition"),
             it.get("status") or "approved", it.get("tags") or [], now))
        conn.commit()
        norm_to_cid[norm] = cur.lastrowid
        added += 1
    _stat("candidates", added, skipped)

    # ---- 3. relations（source candidate name + target_name 扁平结构）----
    added = skipped = 0
    cname_to_cid = {r["name"]: r["id"] for r in conn.execute(
        "SELECT id, name FROM candidates").fetchall()}
    existing_rels = {(r["source_name"], r["target_name"], r["relation_type"])
                     for r in conn.execute(
        "SELECT ca.name AS source_name, r.target_name, r.relation_type"
        " FROM relations r JOIN candidates ca ON ca.id=r.source_id").fetchall()}
    for it in sections.get("relations") or []:
        sn, tn, rt = it.get("source_name"), it.get("target_name"), it.get("relation_type")
        key = (sn, tn, rt)
        sid = cname_to_cid.get(sn)
        if not sn or not tn or not rt or not sid or key in existing_rels:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO relations(source_id, target_name, relation_type)"
            " VALUES(?,?,?)", (sid, tn, rt))
        conn.commit()
        existing_rels.add(key)
        added += 1
    _stat("relations", added, skipped)

    # ---- 4. persona_ontology ----
    added = skipped = 0
    for it in sections.get("persona_ontology") or []:
        iid = name_to_id.get(it.get("identity_name"))
        if not iid:
            skipped += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM persona_ontology WHERE identity_id=? AND kind=?"
            " AND name=?", (iid, it.get("kind"), it.get("name"))).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO persona_ontology(identity_id, kind, name, definition,"
            " status, note, created_at) VALUES(?,?,?,?,?,?,?)",
            (iid, it.get("kind"), it.get("name"), it.get("definition"),
             it.get("status") or "active", it.get("note"), now))
        conn.commit()
        added += 1
    _stat("persona_ontology", added, skipped)

    # ---- 5. persona_actions ----
    added = skipped = 0
    for it in sections.get("persona_actions") or []:
        iid = name_to_id.get(it.get("identity_name"))
        if not iid:
            skipped += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM persona_actions WHERE identity_id=? AND name=?",
            (iid, it.get("action_name"))).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO persona_actions(identity_id, name, description,"
            " input_schema, kind, builtin_name, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (iid, it.get("action_name"), it.get("description") or "",
             json.dumps(it.get("input_schema") or {}, ensure_ascii=False),
             it.get("kind") or "builtin",
             it.get("builtin_name"), it.get("status") or "approved", now))
        conn.commit()
        added += 1
    _stat("persona_actions", added, skipped)

    # ---- 6. anchors ----
    added = skipped = 0
    for it in sections.get("anchors") or []:
        iid = name_to_id.get(it.get("identity_name"))
        if not iid:
            skipped += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM anchors WHERE identity_id=? AND name=?",
            (iid, it.get("name"))).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO anchors(identity_id, name, type, definition, status,"
            " created_at) VALUES(?,?,?,?,?,?)",
            (iid, it.get("name"), it.get("type"), it.get("definition"),
             it.get("status"), now))
        conn.commit()
        added += 1
    _stat("anchors", added, skipped)

    # ---- 7. mcp_servers ----
    added = skipped = 0
    mcp_names = {r["name"] for r in conn.execute(
        "SELECT name FROM mcp_servers").fetchall()}
    for it in sections.get("mcp_servers") or []:
        nm = (it.get("name") or "").strip()
        if not nm or nm in mcp_names:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO mcp_servers(name, description, transport, image,"
            " command, port, status, tools, approval_status, source_path,"
            " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (nm, it.get("description") or "", it.get("transport") or "http",
             it.get("image") or "", it.get("command") or "",
             it.get("port") or 0, it.get("status") or "stopped",
             json.dumps(it.get("tools") or [], ensure_ascii=False),
             it.get("approval_status") or "approved",
             it.get("source_path") or "", now))
        conn.commit()
        mcp_names.add(nm)
        added += 1
    _stat("mcp_servers", added, skipped)

    # ---- 8. pipelines（按 name 判重；persona_id 按 name 重映射）----
    added = skipped = 0
    pl_names = {r["name"] for r in conn.execute(
        "SELECT name FROM pipelines").fetchall()}
    for pl in sections.get("pipelines") or []:
        nm = (pl.get("name") or "").strip()
        if not nm or nm in pl_names:
            skipped += 1
            continue
        new_pid = P.create_pipeline(conn, nm, pl.get("description") or "",
                                    pl.get("tags") or [])
        if new_pid is None:
            skipped += 1
            continue
        key_to_nid: dict = {}
        for nd in pl.get("nodes") or []:
            persona_name = nd.get("persona_name")
            persona_id = name_to_id.get(persona_name) if persona_name else None
            nid = P.add_node(conn, new_pid, nd.get("node_key"),
                             persona_id=persona_id,
                             kind=nd.get("kind") or "nominate",
                             step_name=nd.get("step_name") or "",
                             position={"x": nd.get("position_x"),
                                       "y": nd.get("position_y")})
            if nid:
                key_to_nid[nd["node_key"]] = nid
        for rl in pl.get("relations") or []:
            fid = key_to_nid.get(rl.get("from_key"))
            tid = key_to_nid.get(rl.get("to_key"))
            if fid and tid:
                P.add_relation(conn, new_pid, fid, tid, rl.get("relation_type"))
        if pl.get("is_archived"):
            conn.execute("UPDATE pipelines SET is_archived=true WHERE id=?",
                         (new_pid,))
            conn.commit()
        pl_names.add(nm)
        added += 1
    _stat("pipelines", added, skipped)

    return {"stats": stats}
