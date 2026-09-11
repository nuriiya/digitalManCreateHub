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
    # ---- DFMEA 领域动作（design §15.3 的 C1~C4）----
    # 取值优先级链的执行体：查历史(1) → 查表(2) → 问专家(3) → AI 推断(4)。
    # 都是**读证据 / 写结果**的知识型动作，不含任何领域判定逻辑。
    "fmea_history_query": {
        "name": "查询历史 FMEA",
        "description": "按部件名/编号或关键词检索历史 FMEA 库，返回失效模式、后果、S/O/D、措施与出处（取值优先级第 1 级证据，每条带可引用编号）",
        "input_schema": {"type": "object",
                         "properties": {"part": {"type": "string"},
                                        "keyword": {"type": "string"}},
                         "required": []},
    },
    "fmea_ap_table": {
        "name": "查 AP / S-O-D 准则表",
        "description": ("查 AP 行动优先级或 S/O/D 评分准则（取值优先级第 2 级证据）。"
                        "支持三种用法：① 不传参数 → 一次返回 S/O/D **全部准则**（拿打分口径）；"
                        "② 传 severity+occurrence+detection → 查该组合的 AP；"
                        "③ 传 items 数组 → **批量**查多条 AP"),
        "input_schema": {"type": "object",
                         "properties": {"severity": {"type": "integer"},
                                        "occurrence": {"type": "integer"},
                                        "detection": {"type": "integer"},
                                        "dimension": {"type": "string"},
                                        "score": {"type": "integer"},
                                        "items": {"type": "array"}},
                         "required": []},
    },
    "ask_expert": {
        "name": "询问专家数字人",
        "description": "向指定部件专家数字人提问并取回其专业回答（取值优先级第 3 级证据；专家不可再转问其他专家）",
        "input_schema": {"type": "object",
                         "properties": {"expert": {"type": "string"},
                                        "question": {"type": "string"}},
                         "required": ["expert", "question"]},
    },
    "fmea_write_row": {
        "name": "写入 DFMEA 记录",
        "description": ("写入 DFMEA 行并**逐格**标注来源。单行模式直接传字段；"
                        "多行模式传 rows 数组一次落多行（推荐 —— 一张表十几行，"
                        "批量写可避免逐行往返）。来源必须是 history / table / "
                        "expert:<专家名> / ai_inferred / ai_new 之一；AP 以 AP 表为准"),
        "input_schema": {"type": "object",
                         "properties": {
                             "part": {"type": "string"},
                             "function": {"type": "string"},
                             "failure_mode": {"type": "string"},
                             "failure_effect": {"type": "string"},
                             "severity": {"type": "integer"},
                             "failure_cause": {"type": "string"},
                             "occurrence": {"type": "integer"},
                             "prevention_control": {"type": "string"},
                             "detection_control": {"type": "string"},
                             "detection": {"type": "integer"},
                             "ap": {"type": "string"},
                             "action": {"type": "string"},
                             "sources": {"type": "object"},
                             "rows": {"type": "array"}},
                         "required": []},
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
    # DFMEA 领域动作（design §15.3 C1~C4）
    if builtin_name == "fmea_history_query":
        return _exec_fmea_history_query(conn, args)
    if builtin_name == "fmea_ap_table":
        return _exec_fmea_ap_table(conn, args)
    if builtin_name == "ask_expert":
        return _exec_ask_expert(conn, identity_id, args)
    if builtin_name == "fmea_write_row":
        return _exec_fmea_write_row(conn, args)
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
    """校验并返回该数字人工作区内的安全绝对路径（防路径穿越 ../）。"""
    ident_dir = _os.path.abspath(_os.path.join(_WORKDIR_ROOT, f"id_{identity_id}"))
    p = _os.path.abspath(_os.path.join(ident_dir, path or ""))
    if not (p == ident_dir or p.startswith(ident_dir + _os.sep)):
        return ""
    return p


_MCP_PREFIXES = ("mcp_imports/", "/mcp_imports/")


def _mcp_import_path(path: str) -> str:
    """把 MCP 定义 JSON 写到 mcp_imports/ 提案目录（仅 *.json）。

    这是「模型输出 JSON → 自动导入 MCP」的写入侧：数字人（模型）在对话里
    产出 MCP 定义 JSON，通过 write_file 写到这里，后端 scan 目录自动导入。
    仅允许 .json，且限定在 mcp_imports/ 内（防路径穿越到项目其他文件）。
    """
    from . import mcp
    base = _os.path.abspath(str(mcp.IMPORT_DIR))
    rel = path or ""
    for prefix in _MCP_PREFIXES:
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    p = _os.path.abspath(_os.path.join(base, rel))
    if not (p == base or p.startswith(base + _os.sep)):
        return ""
    if not p.lower().endswith(".json"):
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
    # 路径路由：mcp_imports/ 前缀 → MCP 提案目录（仅 .json）；否则 → 工作区
    if path.startswith(_MCP_PREFIXES):
        p = _mcp_import_path(path)
    else:
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


# ---------------- DFMEA 领域动作执行器（design §15.3 C1~C4） ----------------
# 薄封装：领域逻辑在 fmea.py（确定性数据访问 + 来源裁决），这里只做参数搬运。

def _exec_fmea_history_query(conn, args: dict) -> dict:
    from . import fmea
    a = args or {}
    return fmea.history_query(conn, a.get("part", ""), a.get("keyword", ""))


def _exec_fmea_ap_table(conn, args: dict) -> dict:
    from . import fmea
    a = args or {}
    # 批量查 AP（items=[{severity,occurrence,detection}, …]）—— 一次查多条
    items = a.get("items")
    if isinstance(items, list) and items:
        return fmea.ap_lookup_many(conn, items)
    has_sod = all(a.get(k) not in (None, "")
                  for k in ("severity", "occurrence", "detection"))
    if has_sod:
        return fmea.ap_lookup(conn, a["severity"], a["occurrence"], a["detection"])
    dim = (a.get("dimension") or "").strip()
    if dim and dim.lower() not in ("all", "*"):
        return fmea.criterion_lookup(conn, dim, a.get("score"))
    # 无参 / dimension=all -> 一次返回 S/O/D 三张准则表（打分口径）
    return fmea.criteria_all(conn)


def _exec_ask_expert(conn, identity_id: int, args: dict) -> dict:
    """ask 回退的执行体：向指定专家数字人提问，取回其回答。

    设计要点（design §15.3 E1）：
      - 深度限制 —— 专家不能再转问专家（`fmea.MAX_ASK_DEPTH`），防无限递归；
      - 名字解析必须**唯一**，歧义时明确报错而不是猜；
      - 走 `chat.answer`（带本体 + RAG），专家的回答自带其本体依据。
    """
    from . import fmea
    a = args or {}
    expert = (a.get("expert") or "").strip()
    question = (a.get("question") or "").strip()
    if not expert or not question:
        return {"ok": False, "error": "expert 与 question 不能为空"}
    if fmea.ask_depth() >= fmea.MAX_ASK_DEPTH:
        return {"ok": False,
                "error": "询问层数已达上限：专家不能再转问其他专家"}

    eid = None
    if expert.isdigit():
        row = conn.execute(
            "SELECT id, name FROM identities WHERE id=? AND status='approved'",
            (int(expert),)).fetchone()
        if row:
            eid = row["id"]
    if eid is None:
        rows = conn.execute(
            "SELECT id, name FROM identities WHERE status='approved'"
            " AND name LIKE ?", (f"%{expert}%",)).fetchall()
        if len(rows) > 1:
            return {"ok": False, "error": f"专家「{expert}」匹配到多个："
                    + "、".join(r["name"] for r in rows)}
        if len(rows) == 1:
            eid = rows[0]["id"]
    if eid is None:
        return {"ok": False, "error": f"未找到专家数字人「{expert}」"}
    if eid == identity_id:
        return {"ok": False, "error": "不能询问自己"}
    nm = conn.execute("SELECT name FROM identities WHERE id=?",
                      (eid,)).fetchone()["name"]
    # ask 边裁决（design §15.3 E1）：pipeline 场景下引擎按 ask 边注入可询问名单，
    # 问名单外的专家一律拒绝 —— 边不只是画在图上，它约束运行期权限。
    allowed = fmea.allowed_experts()
    if allowed is not None and nm not in allowed:
        return {"ok": False,
                "error": f"「{nm}」不在本节点可询问的专家名单内"
                         f"（可询问：{'、'.join(allowed) or '无'}）"}

    from . import chat
    fmea.push_ask()
    try:
        r = chat.answer(conn, eid, question, use_ontology=True, use_rag=True,
                        provider="llm2")
    finally:
        fmea.pop_ask()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "result": {
        "expert": nm, "expert_id": eid, "answer": r.get("reply") or "",
        "source": f"expert:{nm}"}}


def _exec_fmea_write_row(conn, args: dict) -> dict:
    from . import fmea
    a = args or {}
    # 批量写（rows=[{…}, …]）—— 一次落多行，避免逐行耗尽轮数
    rows = a.get("rows")
    if isinstance(rows, list) and rows:
        return fmea.write_rows(conn, rows)
    return fmea.write_row(conn, a)


def execute_mcp(conn, mcp_server_id: int, mcp_tool_name: str, args: dict) -> dict:
    """调用 MCP stdio 工具（attach → initialize → tools/call），见 mcp.call_tool。"""
    from . import mcp as mcp_mod
    return mcp_mod.call_tool(conn, int(mcp_server_id), mcp_tool_name, args or {})


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
    # 自审修复：原实现声明返回 bool 却无 return（恒返回 None），调用方无法判断成败。
    return cur.rowcount > 0


def bind_mcp_action(conn, identity_id: int, mcp_server_id: int,
                    mcp_tool_name: str, description: str = "") -> dict:
    """用户显式把 MCP 工具绑定到数字人（status 直接 approved，绕过提名-审批）。幂等。"""
    from . import mcp as mcp_mod
    srv = conn.execute("SELECT id, name, approval_status, tools FROM mcp_servers WHERE id=?",
                       (mcp_server_id,)).fetchone()
    if not srv:
        return {"ok": False, "error": "MCP server 不存在"}
    if (srv["approval_status"] or "approved") != "approved":
        return {"ok": False, "error": "MCP 未审批，不能绑定"}
    # 校验工具存在
    tools = srv["tools"] if isinstance(srv["tools"], list) else []
    tool_names = {t.get("name") for t in tools if isinstance(t, dict)}
    if mcp_tool_name not in tool_names:
        return {"ok": False, "error": f"工具 {mcp_tool_name} 不在该 MCP 中"}
    name = f"mcp:{srv['name']}:{mcp_tool_name}"
    exists = conn.execute(
        "SELECT id, status FROM persona_actions WHERE identity_id=? AND name=?",
        (identity_id, name)).fetchone()
    if exists:
        return {"ok": True, "action_id": exists["id"], "existed": True,
                "status": exists["status"], "name": name}
    cur = conn.execute(
        "INSERT INTO persona_actions(identity_id, name, description,"
        " input_schema, kind, mcp_server_id, mcp_tool_name, builtin_name,"
        " status, created_at) VALUES(?,?,?,?,?,?,?,?,'approved',?)",
        (identity_id, name, description or f"MCP {srv['name']} 工具 {mcp_tool_name}",
         json.dumps({}, ensure_ascii=False), "mcp", mcp_server_id, mcp_tool_name,
         None, db.now()))
    conn.commit()
    return {"ok": True, "action_id": cur.lastrowid, "existed": False, "name": name}


def bind_builtin_action(conn, identity_id: int, builtin_name: str,
                        name: str = "", description: str = "") -> dict:
    """用户显式把**内置动作**绑定到数字人（直接 approved，绕过提名-审批）。幂等。

    与 `bind_mcp_action` 对称 —— 补齐「MCP 能显式直绑、builtin 不能」的缺口
    （design §15.5 落地时发现：装配 DFMEA 工程师需要把领域动作确定性地绑上，
    而不必每次都走 LLM 提名 + 人工审批两跳）。显式绑定语义即「用户已裁决」。
    """
    if builtin_name not in BUILTIN_ACTIONS:
        return {"ok": False, "error": f"内置动作不在注册表：{builtin_name}"}
    meta = BUILTIN_ACTIONS[builtin_name]
    act_name = (name or meta["name"]).strip()
    if not act_name or len(act_name) > 64:
        return {"ok": False, "error": "动作名缺失或过长（≤64）"}
    desc = (description or meta["description"])[:300]
    exists = conn.execute(
        "SELECT id, status FROM persona_actions WHERE identity_id=? AND name=?",
        (identity_id, act_name)).fetchone()
    if exists:
        conn.execute("UPDATE persona_actions SET status='approved' WHERE id=?",
                     (exists["id"],))
        conn.commit()
        return {"ok": True, "action_id": exists["id"], "existed": True,
                "status": "approved", "name": act_name}
    cur = conn.execute(
        "INSERT INTO persona_actions(identity_id, name, description,"
        " input_schema, kind, mcp_server_id, mcp_tool_name, builtin_name,"
        " status, created_at) VALUES(?,?,?,?,?,?,?,?,'approved',?)",
        (identity_id, act_name, desc,
         json.dumps(meta["input_schema"], ensure_ascii=False), "builtin",
         None, None, builtin_name, db.now()))
    conn.commit()
    return {"ok": True, "action_id": cur.lastrowid, "existed": False,
            "status": "approved", "name": act_name}


def unbind_action(conn, identity_id: int, action_id: int) -> bool:
    """解绑 persona action（按 identity_id 校验归属）。"""
    row = conn.execute("SELECT identity_id FROM persona_actions WHERE id=?",
                       (action_id,)).fetchone()
    if not row or row["identity_id"] != identity_id:
        return False
    conn.execute("DELETE FROM persona_actions WHERE id=?", (action_id,))
    conn.commit()
    return True


# ---------------- guard (execution-time adjudication) ----------------

def _norm_action_name(s: str) -> str:
    """动作名归一化：去掉**所有空白** + 转小写。

    LLM 在 `<tool_call>` 里写动作名时常漏空格（实测 2026-09-11：注册名是
    「查询历史 FMEA」，LLM 写成「查询历史FMEA」）。若只做精确匹配，动作会被
    误判为「未声明」而拒绝执行 —— DFMEA 链路因此整轮空转、产出 0 行。
    """
    return _re.sub(r"\s+", "", (s or "")).lower()


def _match_action(conn, identity_id: int, name: str):
    """按名匹配 persona action：先精确，再归一化（**唯一**匹配才算，避免歧义误判）。"""
    row = conn.execute(
        "SELECT * FROM persona_actions WHERE identity_id=? AND name=?",
        (identity_id, name)).fetchone()
    if row:
        return dict(row)
    target = _norm_action_name(name)
    if not target:
        return None
    cands = conn.execute(
        "SELECT * FROM persona_actions WHERE identity_id=?",
        (identity_id,)).fetchall()
    hits = [dict(r) for r in cands if _norm_action_name(r["name"]) == target]
    return hits[0] if len(hits) == 1 else None


#: 入参别名兜底：LLM 受提示里的通用示例影响，常把参数名写成 `query`。
#: 缺必填项时按此表补全（**只补不覆盖**）—— 提名归 LLM、对齐归代码。
_ARG_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("ask_expert", "question"): ("query", "q", "text", "prompt", "ask"),
    ("fmea_history_query", "part"): ("query", "q", "part_name"),
    ("fmea_write_row", "failure_mode"): ("mode", "failure", "failure_mode_name"),
    ("fmea_write_row", "sources"): ("source", "source_map"),
}


def guard_action(conn, identity_id: int, name: str, args: dict) -> tuple[bool, str, dict | None]:
    """Deterministic pre-execution gate (guardians): only an approved action
    with valid args may run. Returns (ok, reason, action_row).

    安全边界（执行类动作额外校验）：
      - 执行类动作（exec/fs）必须显式声明且 approved（白名单裁决，默认拒绝）
      - 入参按 JSON Schema required 校验（泛化，不再只查 query）
      - 执行类入参长度封顶（防 prompt 注入/超大 payload）

    名称匹配先精确、再归一化（见 `_match_action`）：LLM 只负责提名，代码负责
    把它对齐到真实的动作记录上（铁律 L1 —— 提名与裁决分离）。
    """
    row = _match_action(conn, identity_id, name)
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
                # 别名兜底（见 _ARG_ALIASES）：补全到真实参数名上，供执行器使用
                for al in _ARG_ALIASES.get((row["builtin_name"], req), ()):
                    av = args.get(al)
                    if av not in (None, ""):
                        args[req] = av
                        break
                val = args.get(req)
            if val is None or (isinstance(val, str) and not val.strip()):
                return False, f"动作入参缺少 {req}", None
        # 执行类动作：入参长度封顶（防超大 payload / prompt 注入）
        if BUILTIN_ACTIONS[row["builtin_name"]].get("category") in ("exec", "fs"):
            for k, v in args.items():
                if isinstance(v, str) and len(v) > 20000:
                    return False, f"动作入参 {k} 超长（>20000 字符）", None
    return True, "", dict(row)
