# -*- coding: utf-8 -*-
"""数字人动作（六元组 actions 维度）。

动作定义借 Claude Agent SDK 的 tool schema：name + description + input_schema
(JSON Schema)。来源 kind = 'builtin'（内置能力：检索本体 / 检索 RAG 资料）或
'mcp'（绑定 MCP 沙盒工具）。

铁律贯彻：LLM 只提名（定义动作 & 对话时提名调用），确定性代码裁决（三关校验 +
guard_action 执行前把关）。未经用户审批的动作不可被调用。
"""
import json

from . import db, llm

# ---------------- built-in actions ----------------
# 数字人自带能力（无需外部 MCP）。handler 签名: (conn, identity_id, args) -> dict

BUILTIN_ACTIONS: dict[str, dict] = {
    "ontology_retrieve": {
        "name": "检索本体",
        "description": "在该数字人的本体段（ontology 段）中检索与查询相关的概念、定义",
        "input_schema": {"type": "object",
                         "properties": {"query": {"type": "string"}},
                         "required": ["query"]},
    },
    "rag_retrieve": {
        "name": "检索 RAG 资料",
        "description": "在已入库的文档语料中检索与查询相关的原文片段",
        "input_schema": {"type": "object",
                         "properties": {"query": {"type": "string"}},
                         "required": ["query"]},
    },
    # ---- 执行类动作（反应式循环的核心）----
    "run_code": {
        "name": "运行代码",
        "description": "在隔离沙箱中执行一段 Python 代码，返回 stdout/stderr（禁网、只读、限资源）",
        "input_schema": {"type": "object",
                         "properties": {"code": {"type": "string"}},
                         "required": ["code"]},
        "category": "exec",
    },
    "run_test": {
        "name": "运行测试",
        "description": "在隔离沙箱中运行代码 + 断言测试，返回 pass/fail 和失败堆栈（写→测→改的核心闭环动作）",
        "input_schema": {"type": "object",
                         "properties": {"code": {"type": "string"},
                                        "test": {"type": "string"},
                                        "entry_point": {"type": "string"}},
                         "required": ["code", "test"]},
        "category": "exec",
    },
    "read_file": {
        "name": "读取文件",
        "description": "读取工作区文件内容（用于调试：读报错相关源码）",
        "input_schema": {"type": "object",
                         "properties": {"path": {"type": "string"}},
                         "required": ["path"]},
        "category": "fs",
    },
    "write_file": {
        "name": "写入文件",
        "description": "把代码写入工作区文件（用于写代码：落盘后可被测试/运行引用）",
        "input_schema": {"type": "object",
                         "properties": {"path": {"type": "string"},
                                        "content": {"type": "string"}},
                         "required": ["path", "content"]},
        "category": "fs",
    },
}

# 动作类别闭集
ACTION_CATEGORIES = ("knowledge", "exec", "fs")

CLOSED_KINDS = ("builtin", "mcp")


def builtin_catalog() -> list[dict]:
    return [{"builtin_name": k, "name": v["name"],
             "description": v["description"], "input_schema": v["input_schema"]}
            for k, v in BUILTIN_ACTIONS.items()]


# ---------------- execution ----------------

def execute_action(conn, identity_id: int, action: dict, args: dict) -> dict:
    """Execute an approved action. Returns {"ok", "result"|"error"}."""
    kind = action["kind"]
    if kind == "builtin":
        return execute_builtin(conn, identity_id, action["builtin_name"], args)
    if kind == "mcp":
        return execute_mcp(conn, action["mcp_server_id"], action["mcp_tool_name"], args)
    return {"ok": False, "error": f"未知动作类型 {kind}"}


def execute_builtin(conn, identity_id: int, builtin_name: str, args: dict) -> dict:
    q = (args or {}).get("query", "") or ""
    if builtin_name == "ontology_retrieve":
        return _exec_ontology_retrieve(conn, identity_id, q)
    if builtin_name == "rag_retrieve":
        return _exec_rag_retrieve(conn, q)
    if builtin_name == "run_code":
        return _exec_run_code(conn, identity_id, args)
    if builtin_name == "run_test":
        return _exec_run_test(conn, identity_id, args)
    if builtin_name == "read_file":
        return _exec_read_file(conn, identity_id, args)
    if builtin_name == "write_file":
        return _exec_write_file(conn, identity_id, args)
    return {"ok": False, "error": f"未知内置动作 {builtin_name}"}


def _exec_ontology_retrieve(conn, identity_id: int, query: str) -> dict:
    q = (query or "").strip().lower()
    rows = conn.execute(
        "SELECT name, definition FROM persona_ontology"
        " WHERE identity_id=? AND status='active' ORDER BY id",
        (identity_id,)).fetchall()
    items = []
    for r in rows:
        nm = (r["name"] or "").lower()
        df = (r["definition"] or "").lower()
        if q and (q in nm or q in df or (len(nm) >= 3 and nm in q)):
            items.append({"name": r["name"], "definition": r["definition"]})
    return {"ok": True, "result": {"hits": len(items), "items": items[:10]}}


def _exec_rag_retrieve(conn, query: str) -> dict:
    from . import chat
    got = chat._rag_snippets(conn, query)
    if got.get("error"):
        return {"ok": False, "error": got["error"]}
    return {"ok": True, "result": {"hits": got.get("hits", 0),
                                   "texts": got.get("texts", [])}}


# ---------------- 执行类动作（反应式循环） ----------------
# 安全边界铁律：所有执行都在一次性 docker 沙箱（禁网/只读/限资源），
# 数字人永远无法触及宿主文件系统或网络。读写文件限定在 per-identity 工作区。

import os as _os
import re as _re

_WORKDIR_ROOT = _os.environ.get("RAG_WORK_DIR", "data") + "/workspace"


def _safe_path(identity_id: int, path: str) -> str:
    """校验并返回工作区内的安全绝对路径（防路径穿越 ../）。"""
    base = _os.path.abspath(_WORKDIR_ROOT)
    p = _os.path.abspath(_os.path.join(base, f"id_{identity_id}", path or ""))
    if not (p == base or p.startswith(base + _os.sep)):
        return ""
    return p


def _exec_run_code(conn, identity_id: int, args: dict) -> dict:
    code = (args or {}).get("code", "") or ""
    if not code.strip():
        return {"ok": False, "error": "code 不能为空"}
    from . import capability
    r = capability.run_code_sandbox(code, timeout=20)
    return {"ok": True, "result": {"exit_ok": r.get("exit_ok"),
                                   "output": r.get("output", "")[:3000]}}


def _exec_run_test(conn, identity_id: int, args: dict) -> dict:
    code = (args or {}).get("code", "") or ""
    test = (args or {}).get("test", "") or ""
    entry = (args or {}).get("entry_point", "solution") or "solution"
    if not code.strip() or not test.strip():
        return {"ok": False, "error": "code 和 test 不能为空"}
    from . import capability
    r = capability.run_test_sandbox(code, test, entry, timeout=20)
    return {"ok": True, "result": {"verdict": r.get("verdict"),
                                   "output": r.get("output", "")[:3000]}}


def _exec_read_file(conn, identity_id: int, args: dict) -> dict:
    path = (args or {}).get("path", "") or ""
    p = _safe_path(identity_id, path)
    if not p:
        return {"ok": False, "error": "非法路径（越出工作区）"}
    try:
        content = open(p, encoding="utf-8").read()
    except FileNotFoundError:
        return {"ok": False, "error": f"文件不存在：{path}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": {"path": path, "content": content[:5000]}}


def _exec_write_file(conn, identity_id: int, args: dict) -> dict:
    path = (args or {}).get("path", "") or ""
    content = (args or {}).get("content", "") or ""
    if not path.strip():
        return {"ok": False, "error": "path 不能为空"}
    p = _safe_path(identity_id, path)
    if not p:
        return {"ok": False, "error": "非法路径（越出工作区）"}
    try:
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": {"path": path, "written": len(content)}}


def execute_mcp(conn, mcp_server_id: int, mcp_tool_name: str, args: dict) -> dict:
    # MCP 工具执行（stdio server 调用）——首版聚焦 builtin，MCP 绑定留扩展。
    return {"ok": False, "error": f"MCP 动作执行尚未实现（server #{mcp_server_id}）"}


# ---------------- nomination (LLM nominates, code adjudicates) ----------------

def _nominate_prompt(mission: str, anchors: list[str], mcp_tools: list[dict]) -> str:
    builtin_lines = " / ".join(f"{b['builtin_name']}（{b['description']}）"
                               for b in builtin_catalog())
    mcp_lines = "\n".join(f"- {t['name']}：{t['description']}"
                          for t in mcp_tools) or "（无）"
    return (
        "你是数字人动作提名器，只提名、不裁决。\n"
        f"数字人使命：{mission or '（未填写）'}\n"
        f"锚点：{'、'.join(anchors) if anchors else '（无）'}\n\n"
        f"可用内置动作：{builtin_lines}\n"
        f"可用 MCP 工具：\n{mcp_lines}\n\n"
        "请根据该数字人的使命，提名它应该拥有的动作（宁缺毋滥，与使命无关的不要提）。\n"
        "对每个动作输出：name（简短动作名）、description（何时调用，一句中文）、"
        "kind（builtin 或 mcp）、builtin_name（kind=builtin 时，必须是上面内置动作之一）、"
        "mcp_server_id / mcp_tool_name（kind=mcp 时，必须是上面 MCP 工具之一）。\n"
        "严格按 JSON 输出：\n"
        '{"actions": [{"name": "...", "description": "...", "kind": "builtin",'
        ' "builtin_name": "ontology_retrieve", "mcp_server_id": null,'
        ' "mcp_tool_name": null}]}\n'
        "若无需动作，输出 {\"actions\": []}。"
    )


def validate_action(a: dict, mcp_tools: list[dict]) -> tuple[bool, str]:
    """Three deterministic gates on a nominated action (LLM only nominates).

    G1 结构关 — name/description 非空且不长；kind 在闭集
    G2 语义关 — builtin_name 必须在注册表；mcp 绑定必须指向真实工具
    G3 冲突关 — 同义 name 去重由调用方处理（这里只查单条合法性）
    """
    if not isinstance(a, dict):
        return False, "not an object"
    name = (a.get("name") or "").strip()
    if not name or len(name) > 64:
        return False, "name 缺失或过长"
    desc = (a.get("description") or "").strip()
    if not desc or len(desc) > 300:
        return False, "description 缺失或过长"
    kind = (a.get("kind") or "").strip()
    if kind not in CLOSED_KINDS:
        return False, f"kind 不在闭集: {kind}"
    if kind == "builtin":
        bn = (a.get("builtin_name") or "").strip()
        if bn not in BUILTIN_ACTIONS:
            return False, f"内置动作不在注册表: {bn}"
    else:  # mcp
        sid = a.get("mcp_server_id")
        tn = (a.get("mcp_tool_name") or "").strip()
        if not any(t["id"] == sid and t["name"] == tn for t in mcp_tools):
            return False, "MCP 绑定指向不存在的工具"
    return True, ""


def nominate_actions(conn, identity_id: int) -> dict:
    """LLM 提名动作（用 GLM 5.2 判断力），三关校验后落库为 pending。"""
    from . import identity
    ident = identity.get_identity(conn, identity_id) \
        if hasattr(identity, "get_identity") else None
    if ident is None:
        row = conn.execute("SELECT id, name, mission FROM identities WHERE id=?",
                           (identity_id,)).fetchone()
        if not row:
            return {"ok": False, "error": f"数字人 #{identity_id} 不存在"}
        ident = dict(row)
    anchors = [r["name"] for r in conn.execute(
        "SELECT name FROM anchors WHERE identity_id=? AND status='approved'",
        (identity_id,)).fetchall()]
    mcp_tools = _mcp_tool_pool(conn)

    if not llm.llm2_configured():
        return {"ok": False, "error": "动作提名需要 GLM（异源判别）——请到设置页填 GLM token"}

    reply = llm.chat2([{"role": "user",
                        "content": _nominate_prompt(ident.get("mission", ""),
                                                    anchors, mcp_tools)}])
    data = llm.extract_json(reply)
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return {"ok": False, "error": "GLM 输出解析失败", "raw": reply[:200]}

    added = rejected = 0
    for a in data["actions"]:
        ok, reason = validate_action(a, mcp_tools)
        if not ok:
            rejected += 1
            continue
        name = a["name"].strip()
        exists = conn.execute(
            "SELECT id FROM persona_actions WHERE identity_id=? AND name=?",
            (identity_id, name)).fetchone()
        if exists:
            rejected += 1
            continue
        kind = a["kind"].strip()
        schema = BUILTIN_ACTIONS[a["builtin_name"]]["input_schema"] \
            if kind == "builtin" else {"type": "object", "properties": {}}
        conn.execute(
            "INSERT INTO persona_actions(identity_id, name, description,"
            " input_schema, kind, mcp_server_id, mcp_tool_name, builtin_name,"
            " status, created_at) VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
            (identity_id, name, a["description"].strip(),
             json.dumps(schema, ensure_ascii=False), kind,
             a.get("mcp_server_id"), a.get("mcp_tool_name"),
             a.get("builtin_name"), db.now()))
        added += 1
    conn.commit()
    return {"ok": True, "added": added, "rejected": rejected}


def _mcp_tool_pool(conn) -> list[dict]:
    # MCP 工具池：首版只返回已注册 server 的占位（stdio 工具列表需运行时枚举）。
    return []


# ---------------- approve / list ----------------

def list_actions(conn, identity_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, identity_id, name, description, input_schema, kind,"
        " mcp_server_id, mcp_tool_name, builtin_name, status, created_at"
        " FROM persona_actions WHERE identity_id=? ORDER BY id",
        (identity_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # psycopg3 已把 JSONB 列解析为 dict，直接透传即可
        d["input_schema"] = d["input_schema"] if isinstance(d["input_schema"], dict) else {}
        out.append(d)
    return out


def approved_actions(conn, identity_id: int) -> list[dict]:
    return [a for a in list_actions(conn, identity_id) if a["status"] == "approved"]


def set_action_status(conn, action_id: int, status: str) -> bool:
    if status not in ("approved", "rejected", "pending"):
        return False
    cur = conn.execute("UPDATE persona_actions SET status=? WHERE id=?",
                       (status, action_id))
    conn.commit()
    return cur.rowcount > 0


# ---------------- guard (execution-time adjudication) ----------------

def guard_action(conn, identity_id: int, name: str, args: dict) -> tuple[bool, str, dict | None]:
    """Deterministic pre-execution gate (guardians): only an approved action
    with valid args may run. Returns (ok, reason, action_row).

    安全边界（执行类动作额外校验）：
      - 执行类动作（exec/fs）必须显式声明且 approved（白名单裁决，默认拒绝）
      - 入参按 JSON Schema required 校验（泛化，不再只查 query）
      - 执行类入参长度封顶（防 prompt 注入/超大 payload）
    """
    row = conn.execute(
        "SELECT * FROM persona_actions WHERE identity_id=? AND name=?",
        (identity_id, name)).fetchone()
    if not row:
        return False, f"数字人未声明动作「{name}」", None
    if row["status"] != "approved":
        return False, f"动作「{name}」未通过审批", None
    if not isinstance(args, dict):
        return False, "动作入参必须是对象", None
    # 内置动作：按 schema required 泛化校验入参
    if row["kind"] == "builtin" and row["builtin_name"] in BUILTIN_ACTIONS:
        schema = BUILTIN_ACTIONS[row["builtin_name"]]["input_schema"]
        for req in schema.get("required", []):
            val = args.get(req)
            if val is None or (isinstance(val, str) and not val.strip()):
                return False, f"动作入参缺少 {req}", None
        # 执行类动作：入参长度封顶（防超大 payload / prompt 注入）
        if BUILTIN_ACTIONS[row["builtin_name"]].get("category") in ("exec", "fs"):
            for k, v in args.items():
                if isinstance(v, str) and len(v) > 20000:
                    return False, f"动作入参 {k} 超长（>20000 字符）", None
    return True, "", dict(row)
