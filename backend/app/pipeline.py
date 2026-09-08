# -*- coding: utf-8 -*-
"""pipeline 编排（一等公民实体，对称于数字人本体库）。

数据模型对称：
  数字人本体 = persona_ontology(实体) + relations(关系)
  pipeline 本体 = pipeline_nodes(数字人节点·实体) + pipeline_relations(数字人关系·关系) + tags(标签)

铁律：
  - 流程设计师(LLM)只提名编排图与修改；编排图校验器(纯代码)裁决；用户终审
  - 关系类型闭集枚举，LLM 提名越界直接丢弃
  - 节点引用数字人(persona_id)不拷贝；数字人本体独立演进
  - 批准 pipeline 时 tags 生效为通路库检索标签（标签优先 0 LLM）
"""
import json

from . import db, jobs

# ---------------- 关系类型闭集（语义化） ----------------
RELATION_DESIGN = "design"        # 设计 →（流程设计师设计编排）
RELATION_SUPPLY = "supply"        # 供给知识 →（专业数字人供给领域知识）
RELATION_REVIEW = "review"        # 复核/审核门 →（复核人复核产出，过了才能流转）
RELATION_HANDOFF = "handoff"      # 交接 →（通用 A 交给 B）
RELATION_COMPOSE = "compose"      # 组装 →（建数字人工具组装数字人）
RELATION_ASK = "ask"              # 询问 →（下游向上游提问细化，层级关系）
RELATION_TYPES = (RELATION_DESIGN, RELATION_SUPPLY, RELATION_REVIEW,
                  RELATION_HANDOFF, RELATION_COMPOSE, RELATION_ASK)

# ---------------- 节点 kind ----------------
KIND_NOMINATE = "nominate"            # LLM 提名节点（数字人）
KIND_DETERMINISTIC = "deterministic"  # 确定性节点（Function）
NODE_KINDS = (KIND_NOMINATE, KIND_DETERMINISTIC)

# ---------------- 状态 ----------------
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_DEPRECATED = "deprecated"
PIPELINE_STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_DEPRECATED)

# ---------------- 修改提名 action（对话/编辑改 pipeline） ----------------
CHANGE_ADD_NODE = "add_node"
CHANGE_REMOVE_NODE = "remove_node"
CHANGE_ADD_RELATION = "add_relation"
CHANGE_REMOVE_RELATION = "remove_relation"
CHANGE_UPDATE_HANDOFF = "update_handoff"
CHANGE_SET_TAGS = "set_tags"
CHANGE_ACTIONS = (CHANGE_ADD_NODE, CHANGE_REMOVE_NODE, CHANGE_ADD_RELATION,
                  CHANGE_REMOVE_RELATION, CHANGE_UPDATE_HANDOFF, CHANGE_SET_TAGS)

MAX_NAME_LEN = 200


# ---------------- query helpers ----------------

def _nodes(conn, pipeline_id) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM pipeline_nodes WHERE pipeline_id=? ORDER BY id",
        (pipeline_id,)).fetchall()]


def _relations(conn, pipeline_id) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM pipeline_relations WHERE pipeline_id=? ORDER BY id",
        (pipeline_id,)).fetchall()]


def list_pipelines(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM pipelines ORDER BY id DESC").fetchall():
        d = dict(r)
        d["tags"] = list(d.get("tags") or [])
        d["nodes"] = _nodes(conn, d["id"])
        d["relations"] = _relations(conn, d["id"])
        out.append(d)
    return out


def get_pipeline(conn, pipeline_id) -> dict | None:
    r = conn.execute("SELECT * FROM pipelines WHERE id=?",
                     (pipeline_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["tags"] = list(d.get("tags") or [])
    d["nodes"] = _nodes(conn, pipeline_id)
    d["relations"] = _relations(conn, pipeline_id)
    return d


# ---------------- pipeline CRUD ----------------

def create_pipeline(conn, name, description="", tags=None) -> int | None:
    name = str(name or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    cur = conn.execute(
        "INSERT INTO pipelines(name, description, version, status, tags, created_at)"
        " VALUES(?,?,1,?,?,?)",
        (name, str(description or "").strip(), STATUS_DRAFT, tags, db.now()))
    conn.commit()
    return cur.lastrowid


def update_pipeline(conn, pipeline_id, patch: dict) -> bool:
    sets, vals = [], []
    if "name" in patch:
        n = str(patch["name"] or "").strip()
        if not n or len(n) > MAX_NAME_LEN:
            return False
        sets.append("name=?"); vals.append(n)
    if "description" in patch:
        sets.append("description=?"); vals.append(str(patch["description"] or "").strip())
    if "tags" in patch:
        t = patch["tags"]
        if not isinstance(t, list):
            return False
        sets.append("tags=?"); vals.append([str(x).strip() for x in t if str(x).strip()])
    if "entry_node_id" in patch:
        sets.append("entry_node_id=?"); vals.append(patch["entry_node_id"])
    if "exit_node_id" in patch:
        sets.append("exit_node_id=?"); vals.append(patch["exit_node_id"])
    if not sets:
        return False
    vals.append(pipeline_id)
    conn.execute(f"UPDATE pipelines SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return True


def delete_pipeline(conn, pipeline_id) -> bool:
    cur = conn.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- node CRUD ----------------

def add_node(conn, pipeline_id, node_key, persona_id=None,
             kind=KIND_NOMINATE, step_name="", position=None) -> int | None:
    node_key = str(node_key or "").strip()
    if not node_key or kind not in NODE_KINDS:
        return None
    x = y = None
    if isinstance(position, dict):
        x, y = position.get("x"), position.get("y")
    cur = conn.execute(
        "INSERT INTO pipeline_nodes(pipeline_id, node_key, persona_id, kind,"
        " step_name, position_x, position_y, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (pipeline_id, node_key, persona_id, kind,
         str(step_name or "").strip(), x, y, db.now()))
    conn.commit()
    return cur.lastrowid


def update_node(conn, node_id, patch: dict) -> bool:
    sets, vals = [], []
    if "node_key" in patch:
        k = str(patch["node_key"] or "").strip()
        if not k:
            return False
        sets.append("node_key=?"); vals.append(k)
    if "persona_id" in patch:
        sets.append("persona_id=?"); vals.append(patch["persona_id"])
    if "kind" in patch:
        if patch["kind"] not in NODE_KINDS:
            return False
        sets.append("kind=?"); vals.append(patch["kind"])
    if "step_name" in patch:
        sets.append("step_name=?"); vals.append(str(patch["step_name"] or "").strip())
    if "position" in patch and isinstance(patch["position"], dict):
        sets.append("position_x=?"); vals.append(patch["position"].get("x"))
        sets.append("position_y=?"); vals.append(patch["position"].get("y"))
    if not sets:
        return False
    vals.append(node_id)
    conn.execute(f"UPDATE pipeline_nodes SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return True


def remove_node(conn, node_id) -> bool:
    cur = conn.execute("DELETE FROM pipeline_nodes WHERE id=?", (node_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- relation CRUD ----------------

def add_relation(conn, pipeline_id, from_node_id, to_node_id, relation_type,
                 handoff_type="", handoff_schema="") -> int | None:
    if relation_type not in RELATION_TYPES:
        return None
    if from_node_id == to_node_id:
        return None
    cur = conn.execute(
        "INSERT INTO pipeline_relations(pipeline_id, from_node_id, to_node_id,"
        " relation_type, handoff_type, handoff_schema, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (pipeline_id, from_node_id, to_node_id, relation_type,
         str(handoff_type or "").strip(), str(handoff_schema or "").strip(),
         db.now()))
    conn.commit()
    return cur.lastrowid


def remove_relation(conn, relation_id) -> bool:
    cur = conn.execute("DELETE FROM pipeline_relations WHERE id=?", (relation_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- 校验器（确定性代码，LLM 无终审权） ----------------

def validate_pipeline(conn, pipeline_id) -> list[str]:
    """确定性校验编排图：引用悬空 / 环检测 / 闭集。返回错误列表，空=通过。"""
    p = conn.execute("SELECT * FROM pipelines WHERE id=?",
                     (pipeline_id,)).fetchone()
    if not p:
        return ["pipeline not found"]
    nodes = _nodes(conn, pipeline_id)
    rels = _relations(conn, pipeline_id)
    node_ids = {n["id"] for n in nodes}
    errors = []

    for n in nodes:
        if n["kind"] not in NODE_KINDS:
            errors.append(f"node {n['id']}: kind 越界 {n['kind']}")
        # 引用悬空：persona_id 必须存在（若填了）
        if n["persona_id"] is not None:
            exists = conn.execute("SELECT 1 FROM identities WHERE id=?",
                                  (n["persona_id"],)).fetchone()
            if not exists:
                errors.append(f"node {n['id']}: persona_id {n['persona_id']} 不存在")

    for r in rels:
        if r["relation_type"] not in RELATION_TYPES:
            errors.append(f"relation {r['id']}: 关系类型越界 {r['relation_type']}")
        if r["from_node_id"] not in node_ids:
            errors.append(f"relation {r['id']}: from_node {r['from_node_id']} 不存在")
        if r["to_node_id"] not in node_ids:
            errors.append(f"relation {r['id']}: to_node {r['to_node_id']} 不存在")

    if p["entry_node_id"] and p["entry_node_id"] not in node_ids:
        errors.append(f"entry_node {p['entry_node_id']} 不存在")
    if p["exit_node_id"] and p["exit_node_id"] not in node_ids:
        errors.append(f"exit_node {p['exit_node_id']} 不存在")

    adj = {nid: [] for nid in node_ids}
    for r in rels:
        # ask（询问）是下游问上游的反向边，不参与流程 DAG 环检测
        if r["relation_type"] == RELATION_ASK:
            continue
        if r["from_node_id"] in adj and r["to_node_id"] in adj:
            adj[r["from_node_id"]].append(r["to_node_id"])
    if _has_cycle(adj):
        errors.append("编排图存在环（当前只支持 DAG）")

    return errors


def _has_cycle(adj: dict) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in adj}

    def dfs(u):
        color[u] = GRAY
        for v in adj[u]:
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    for n in adj:
        if color[n] == WHITE and dfs(n):
            return True
    return False


# ---------------- 批准 + 打标签入本体库 ----------------

def approve_pipeline(conn, pipeline_id) -> bool:
    """批准 pipeline：status draft→approved；tags 生效为通路库检索标签。

    调用方必须先跑 validate_pipeline 确认无误。"""
    p = conn.execute("SELECT id FROM pipelines WHERE id=?",
                     (pipeline_id,)).fetchone()
    if not p:
        return False
    cur = conn.execute("UPDATE pipelines SET status=? WHERE id=?",
                       (STATUS_APPROVED, pipeline_id))
    conn.commit()
    return cur.rowcount > 0


# ---------------- 对话/编辑改 pipeline：提名 → 审批 → merge ----------------

def nominate_changes(conn, pipeline_id, changes: list) -> int | None:
    """把设计师(LLM)产出的修改提名落库为 pending。

    changes = [{action, payload, reason?}, ...]；action 越界直接丢弃。
    返回第一个 change 的 id（0 个合法提名时返回 None）。"""
    if not isinstance(changes, list):
        return None
    first = None
    for c in changes:
        if not isinstance(c, dict):
            continue
        action = c.get("action")
        if action not in CHANGE_ACTIONS:
            continue
        payload = c.get("payload") or {}
        cur = conn.execute(
            "INSERT INTO pipeline_changes(pipeline_id, action, payload, status,"
            " reason, created_at) VALUES(?,?,?,'pending',?,?)",
            (pipeline_id, action, json.dumps(payload, ensure_ascii=False),
             str(c.get("reason") or "").strip(), db.now()))
        if first is None:
            first = cur.lastrowid
    conn.commit()
    return first


def list_changes(conn, pipeline_id) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT * FROM pipeline_changes WHERE pipeline_id=? ORDER BY id",
        (pipeline_id,)).fetchall():
        d = dict(r)
        payload = d.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        d["payload"] = payload or {}
        out.append(d)
    return out


def apply_change(conn, change_id) -> bool:
    """用户批准修改 → 应用（merge）。payload 决定实际动作。"""
    c = conn.execute("SELECT * FROM pipeline_changes WHERE id=?",
                     (change_id,)).fetchone()
    if not c or c["status"] != "pending":
        return False
    payload = c["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    payload = payload or {}
    action = c["action"]
    pid = c["pipeline_id"]
    ok = False
    if action == CHANGE_ADD_NODE:
        nid = add_node(conn, pid, payload.get("node_key", ""),
                       payload.get("persona_id"),
                       payload.get("kind", KIND_NOMINATE),
                       payload.get("step_name", ""),
                       payload.get("position"))
        ok = nid is not None
    elif action == CHANGE_REMOVE_NODE:
        ok = remove_node(conn, payload.get("node_id"))
    elif action == CHANGE_ADD_RELATION:
        from_id = payload.get("from_node_id")
        to_id = payload.get("to_node_id")
        # 兼容 LLM 提名用 node_key（字符串）而非数字 id 的情况
        if from_id is None and payload.get("from_node_key"):
            r = conn.execute(
                "SELECT id FROM pipeline_nodes WHERE pipeline_id=? AND node_key=?",
                (pid, payload["from_node_key"])).fetchone()
            from_id = r["id"] if r else None
        if to_id is None and payload.get("to_node_key"):
            r = conn.execute(
                "SELECT id FROM pipeline_nodes WHERE pipeline_id=? AND node_key=?",
                (pid, payload["to_node_key"])).fetchone()
            to_id = r["id"] if r else None
        if from_id is None or to_id is None:
            ok = False
        else:
            rid = add_relation(conn, pid, from_id, to_id,
                               payload.get("relation_type"),
                               payload.get("handoff_type", ""),
                               payload.get("handoff_schema", ""))
            ok = rid is not None
    elif action == CHANGE_REMOVE_RELATION:
        ok = remove_relation(conn, payload.get("relation_id"))
    elif action == CHANGE_UPDATE_HANDOFF:
        cur = conn.execute(
            "UPDATE pipeline_relations SET handoff_type=?, handoff_schema=? WHERE id=?",
            (payload.get("handoff_type", ""), payload.get("handoff_schema", ""),
             payload.get("relation_id")))
        conn.commit()
        ok = cur.rowcount > 0
    elif action == CHANGE_SET_TAGS:
        cur = conn.execute("UPDATE pipelines SET tags=? WHERE id=?",
                           (payload.get("tags", []), pid))
        conn.commit()
        ok = cur.rowcount > 0
    if ok:
        conn.execute("UPDATE pipeline_changes SET status='approved' WHERE id=?",
                     (change_id,))
        conn.commit()
    return ok


def reject_change(conn, change_id) -> bool:
    cur = conn.execute(
        "UPDATE pipeline_changes SET status='rejected' WHERE id=? AND status='pending'",
        (change_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- 执行引擎（复用 jobs.py 骨架，泛化「一键流水线」） ----------------

def topo_sort(nodes: list[dict], relations: list[dict]) -> list[dict]:
    """拓扑排序节点（DAG）。返回按执行顺序排列的节点列表（环/孤立节点补尾部）。"""
    node_by_id = {n["id"]: n for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    indeg = {n["id"]: 0 for n in nodes}
    for r in relations:
        # ask（询问）不参与执行顺序（是反向询问边，非流程边）
        if r["relation_type"] == RELATION_ASK:
            continue
        if r["from_node_id"] in adj and r["to_node_id"] in adj:
            adj[r["from_node_id"]].append(r["to_node_id"])
            indeg[r["to_node_id"]] += 1
    order: list[int] = []
    queue = [n["id"] for n in nodes if indeg[n["id"]] == 0]
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for v in adj[nid]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    for n in nodes:
        if n["id"] not in order:
            order.append(n["id"])
    return [node_by_id[nid] for nid in order]


def create_run(conn, pipeline_id, job_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_runs(pipeline_id, job_id, status, created_at)"
        " VALUES(?,?,'running',?)", (pipeline_id, job_id, db.now()))
    conn.commit()
    return cur.lastrowid


def store_handoff(conn, run_id, node_id, handoff) -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_run_handoffs(run_id, node_id, handoff, created_at)"
        " VALUES(?,?,?,?)", (run_id, node_id, str(handoff or ""), db.now()))
    conn.commit()
    return cur.lastrowid


def collect_inputs(conn, run_id, node, nodes, relations) -> list[str]:
    """收集该节点的所有上游节点的交接物（按 relations 的 from → to）。"""
    inputs = []
    upstream = [r["from_node_id"] for r in relations
                if r["to_node_id"] == node["id"]]
    for uid in upstream:
        rows = conn.execute(
            "SELECT handoff FROM pipeline_run_handoffs WHERE run_id=? AND node_id=?"
            " ORDER BY id", (run_id, uid)).fetchall()
        for r in rows:
            if r["handoff"]:
                inputs.append(r["handoff"])
    return inputs


def _run_build_persona(conn, parent_job_id) -> str:
    """deterministic「建数字人」= ingest → ontology → assemble 三步子 job 串行。"""
    from . import ingest, ontology, assembly, settings_store
    path = settings_store.load_settings().get("work_dir", "")
    if not path:
        return json.dumps({"error": "work_dir 未设置"}, ensure_ascii=False)
    ing = jobs.create_job(conn, "ingest", 0, detail=path, parent_id=parent_job_id)
    jobs.run_in_background(ing, ingest.ingest_workdir, path)
    if jobs.wait_job(conn, ing, timeout=3600) != "done":
        return json.dumps({"error": "步骤「添加资料」未完成"}, ensure_ascii=False)
    n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    ont = jobs.create_job(conn, "ontology", n, detail="EDC-lite", parent_id=parent_job_id)
    jobs.run_in_background(ont, ontology.run_extraction)
    if jobs.wait_job(conn, ont, timeout=3600) != "done":
        return json.dumps({"error": "步骤「本体提取」未完成"}, ensure_ascii=False)
    row = conn.execute(
        "SELECT id FROM identities WHERE status='approved' ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        asm = jobs.create_job(conn, "assemble", 0, detail="装配", parent_id=parent_job_id)
        jobs.run_in_background(asm, assembly.run_assembly, row["id"])
        if jobs.wait_job(conn, asm, timeout=3600) != "done":
            return json.dumps({"error": "步骤「装配」未完成"}, ensure_ascii=False)
    return json.dumps({"built": True, "identity_id": row["id"] if row else None},
                      ensure_ascii=False)


def _run_deterministic(conn, node, inputs, parent_job_id) -> str:
    """deterministic 节点执行：内置 Function 映射表（第一版仅「建数字人」）。"""
    step = (node.get("step_name") or "").strip()
    if "建数字人" in step or "建专业数字人" in step:
        return _run_build_persona(conn, parent_job_id)
    return json.dumps({"note": f"deterministic 节点「{step}」执行尚未实现"},
                      ensure_ascii=False)


def _run_nominate(conn, node, inputs) -> str:
    """nominate 节点执行 = 数字人 chat.answer（自带 tool-use loop）。"""
    from . import chat
    persona_id = node.get("persona_id")
    step = (node.get("step_name") or node.get("node_key") or "").strip()
    if not persona_id:
        return json.dumps({"note": f"nominate 节点「{step}」未绑定数字人，跳过"},
                          ensure_ascii=False)
    msg = f"【编排任务】{step}\n"
    if inputs:
        msg += "\n上游交接物：\n" + "\n".join(f"- {x[:500]}" for x in inputs)
    msg += "\n请完成本步骤并产出可传递给下游的交接物。"
    r = chat.answer(conn, persona_id, msg, use_ontology=True, use_rag=True,
                    provider="llm2")
    if not r.get("ok"):
        return json.dumps({"error": r.get("error")}, ensure_ascii=False)
    return r.get("reply") or ""


def run_pipeline_execution(conn, job_id, pipeline_id) -> None:
    """执行引擎：父 job 拓扑调度节点，交接物传递，progress 记录 checkpoint。

    作为 jobs.run_in_background 的 fn(conn, job_id) 调用。deterministic 节点走
    内置映射表，nominate 节点走数字人 tool-use loop。"""
    p = get_pipeline(conn, pipeline_id)
    if p is None:
        jobs.finish_job(conn, job_id, ok=False, error="pipeline not found")
        return
    errors = validate_pipeline(conn, pipeline_id)
    if errors:
        jobs.finish_job(conn, job_id, ok=False, error="；".join(errors))
        return
    order = topo_sort(p["nodes"], p["relations"])
    if not order:
        jobs.finish_job(conn, job_id, ok=False, error="pipeline 无节点")
        return
    run_id = create_run(conn, pipeline_id, job_id)
    jobs.update_progress(conn, job_id, 0, len(order))
    for i, node in enumerate(order):
        jobs.emit(conn, job_id, "pipeline.node",
                  {"node_key": node["node_key"], "step": node.get("step_name"),
                   "kind": node["kind"], "status": "running", "index": i})
        inputs = collect_inputs(conn, run_id, node, p["nodes"], p["relations"])
        if node["kind"] == KIND_DETERMINISTIC:
            output = _run_deterministic(conn, node, inputs, job_id)
        else:
            output = _run_nominate(conn, node, inputs)
        store_handoff(conn, run_id, node["id"], output)
        jobs.emit(conn, job_id, "pipeline.node",
                  {"node_key": node["node_key"], "status": "done", "index": i})
        jobs.update_progress(conn, job_id, i + 1, len(order))
        conn.execute("UPDATE pipeline_runs SET current_node_id=?, status='running'"
                     " WHERE id=?", (node["id"], run_id))
        conn.commit()
    conn.execute("UPDATE pipeline_runs SET status='done' WHERE id=?", (run_id,))
    conn.commit()
    jobs.finish_job(conn, job_id, ok=True)


# ---------------- 对话命令：自然语言 → pipeline 设计 → 落库（draft 待审批） ----------------
def _extract_json(text: str):
    """从 LLM 输出提取 JSON（容错 markdown 代码块 + 前后杂文）。"""
    import re
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def generate_from_request(conn, request: str, provider: str = "llm") -> dict:
    """对话命令：自然语言需求 → LLM 设计 pipeline → 落库为 draft（待用户审批）。"""
    from . import llm
    request = (request or "").strip()
    if not request:
        return {"ok": False, "error": "需求描述不能为空"}
    personas = [dict(r) for r in conn.execute(
        "SELECT id, name, category FROM identities WHERE status='approved' ORDER BY id"
    ).fetchall()]
    persona_str = "\n".join(
        f"  - id={p['id']} name={p['name']} category={p['category']}" for p in personas
    ) or "（无）"
    prompt = (
        "你是 pipeline 编排设计师。根据需求设计一个数字人协作的 pipeline "
        "（节点 + 关系），输出 JSON 落库待审批。\n\n"
        f"需求：{request}\n\n"
        f"可用数字人：\n{persona_str}\n\n"
        "严格按以下 schema 输出 JSON（不要解释、不要 markdown 代码块）：\n"
        '{"name":"英文短名","description":"中文描述","tags":["tag1"],'
        '"nodes":[{"node_key":"step1","persona_id":1,"step_name":"步骤名"}],'
        '"relations":[{"from_node_key":"step1","to_node_key":"step2",'
        '"relation_type":"supply"}]}\n\n'
        "要求：node_key 唯一英文小写连字符；persona_id 必须是上面可用数字人的 id；"
        f"relation_type ∈ {RELATION_TYPES}；关系形成 DAG 不能成环。"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        text = (llm.chat(messages, temperature=0.2) if provider == "llm"
                else llm.chat2(messages))
    except Exception as e:
        return {"ok": False, "error": f"LLM 生成失败：{e}"}
    data = _extract_json(text)
    if data is None:
        return {"ok": False, "error": "未能解析 LLM 输出的 JSON", "raw": text[:500]}
    name = (data.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name 必填"}
    nodes = data.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        return {"ok": False, "error": "nodes 不能为空"}
    persona_ids = {p["id"] for p in personas}
    for n in nodes:
        pid = n.get("persona_id")
        if pid not in persona_ids:
            return {"ok": False,
                    "error": f"节点 {n.get('node_key')} 的 persona_id {pid} 不在可用数字人中"}
    relations = data.get("relations") or []
    for r in relations:
        if r.get("relation_type") not in RELATION_TYPES:
            return {"ok": False,
                    "error": f"关系 {r} 的 relation_type 越界"}
    try:
        pipeline_id = create_pipeline(conn, name, data.get("description", ""),
                                     data.get("tags") or [])
    except Exception as e:
        return {"ok": False, "error": f"创建 pipeline 失败：{e}"}
    key_to_id: dict[str, int] = {}
    for n in nodes:
        nid = add_node(conn, pipeline_id, n["node_key"], n.get("persona_id"),
                       kind=KIND_NOMINATE, step_name=n.get("step_name", ""))
        if nid is None:
            conn.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
            return {"ok": False, "error": f"创建节点 {n.get('node_key')} 失败"}
        key_to_id[n["node_key"]] = nid
    for r in relations:
        fid = key_to_id.get(r.get("from_node_key"))
        tid = key_to_id.get(r.get("to_node_key"))
        if not fid or not tid:
            continue
        add_relation(conn, pipeline_id, fid, tid, r["relation_type"])
    if nodes:
        update_pipeline(conn, pipeline_id, {
            "entry_node_id": key_to_id[nodes[0]["node_key"]],
            "exit_node_id": key_to_id[nodes[-1]["node_key"]],
        })
    return {"ok": True, "pipeline_id": pipeline_id, "name": name,
            "status": STATUS_DRAFT, "nodes": len(nodes), "relations": len(relations)}
