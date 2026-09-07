# -*- coding: utf-8 -*-
"""能力测试沙箱：能力题（任务 + 隐藏测试）+ 可执行验证。

判定铁律（能力型测试的核心）：代码对不对，跑测试说了算（可执行验证），
不是 LLM 猜。数字人生成的代码丢进一次性 python 容器跑 assert，绿=通过。

能力题来源：HumanEval 164 题（backend/data/capability_tasks.json），由
convert_humaneval.py 生成，import_tasks 幂等入库。
"""
import os

from . import db

# 一次性测试容器镜像（国内环境走镜像前缀，见 compose 的 CAPABILITY_SANDBOX_IMAGE）
SANDBOX_IMAGE = os.environ.get("CAPABILITY_SANDBOX_IMAGE", "python:3.13-slim")

VERDICTS = ("pending", "pass", "fail", "error")


# ---------------- 能力题 ----------------

def import_tasks(conn, tasks: list[dict]) -> int:
    """幂等导入能力题（task_key 已存在则跳过）。返回新增数。"""
    added = 0
    for t in tasks:
        key = str(t.get("id") or t.get("task_key") or "").strip()
        if not key:
            continue
        exists = conn.execute(
            "SELECT 1 FROM capability_tasks WHERE task_key=?", (key,)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO capability_tasks(task_key, category, persona_role,"
            " prompt, entry_point, test, canonical_solution, source, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (key,
             str(t.get("category") or "code_generation"),
             str(t.get("persona_role") or "code_engineer"),
             t.get("prompt") or "",
             t.get("entry_point") or "",
             t.get("test") or "",
             t.get("canonical_solution") or "",
             t.get("source") or "",
             db.now()))
        added += 1
    conn.commit()
    return added


def list_tasks(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, task_key, category, persona_role, entry_point, source"
        " FROM capability_tasks ORDER BY id").fetchall()]


def get_task(conn, task_id) -> dict | None:
    r = conn.execute("SELECT * FROM capability_tasks WHERE id=?",
                     (task_id,)).fetchone()
    return dict(r) if r else None


# ---------------- 可执行验证 ----------------

def _build_runner_code(task: dict, code: str) -> str:
    """拼接：数字人生的代码 + 隐藏测试(check 定义) + check 调用。"""
    return f"{code}\n\n{task['test']}\n\ncheck({task['entry_point']})\n"


def _run_in_container(code: str, timeout=30) -> tuple[bool, str]:
    """一次性 python 容器跑代码，返回 (ok, output)。

    通过挂载的 /var/run/docker.sock 用 docker-py SDK 直接调宿主 daemon，
    不依赖 docker CLI。"""
    import docker
    try:
        client = docker.from_env()
        result = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-c", code],
            remove=True, stdout=True, stderr=True,
        )
        return True, (result or b"").decode("utf-8", errors="replace").strip()
    except docker.errors.ContainerError as e:
        # 退出码非 0（assert 失败或运行时异常）→ fail
        out = (e.stderr or b"").decode("utf-8", errors="replace")
        return False, out.strip()
    except docker.errors.ImageNotFound:
        return False, f"image not found: {SANDBOX_IMAGE}"
    except docker.errors.APIError as e:
        return False, f"docker API error: {e}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def run_task(conn, task_id, code: str, identity_id=None, timeout=30) -> dict:
    """跑一道能力题：可执行验证数字人生的代码。返回 run 结果。"""
    task = get_task(conn, task_id)
    if not task:
        return {"error": "task not found"}
    runner = _build_runner_code(task, code)
    ok, output = _run_in_container(runner, timeout=timeout)
    verdict = "pass" if ok else "fail"
    cur = conn.execute(
        "INSERT INTO capability_runs(task_id, identity_id, code, verdict,"
        " output, created_at) VALUES(?,?,?,?,?,?)",
        (task_id, identity_id, code, verdict, output[:4000], db.now()))
    conn.commit()
    return {"run_id": cur.lastrowid, "task_id": task_id,
            "entry_point": task["entry_point"], "verdict": verdict,
            "output": output[:2000]}


def _extract_code(reply: str) -> str:
    """从数字人回答里提取纯代码（数字人常带解释 + markdown 代码块）。"""
    import re
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", reply, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 无代码块：从 def / from / import 起始行截断（去掉开头的解释）
    lines = reply.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("def ", "from ", "import ", "class ")):
            return "\n".join(lines[i:]).strip()
    return reply.strip()


def run_for_identity(conn, identity_id, task_id, provider="llm2") -> dict:
    """让数字人（identity）针对能力题生成代码，再可执行验证。

    数字人解题 = 用它的本体+prompt 生成代码（闭卷作答），判定 = 跑 assert
    （可执行验证，开卷裁决）——对应考核双 LLM 的能力型版本。
    """
    task = get_task(conn, task_id)
    if not task:
        return {"error": "task not found"}
    from . import chat
    try:
        result = chat._generate(
            conn, identity_id, task["prompt"], use_ontology=True,
            provider=provider, ollama_model=None,
            concept_fallback=False, use_rag=False, session_id=None)
    except Exception as e:  # noqa: BLE001
        return {"error": f"generation failed: {e}"}
    if not result.get("ok"):
        return {"error": result.get("error") or "generation failed"}
    code = _extract_code(result["reply"])
    r = run_task(conn, task_id, code, identity_id=identity_id)
    r["reply"] = code[:2000]
    return r


def run_stats(conn, identity_id=None) -> dict:
    """能力测试通过率统计（可选按数字人过滤）。"""
    where, params = "", []
    if identity_id is not None:
        where = "WHERE identity_id=?"
        params = [identity_id]
    rows = conn.execute(
        f"SELECT verdict, COUNT(*) AS n FROM capability_runs {where}"
        " GROUP BY verdict", params).fetchall()
    total = sum(r["n"] for r in rows)
    by = {r["verdict"]: r["n"] for r in rows}
    passed = by.get("pass", 0)
    return {
        "total": total,
        "pass": passed,
        "fail": by.get("fail", 0),
        "error": by.get("error", 0),
        "pass_rate": round(passed / total, 4) if total else None,
    }


# ---------------- 能力沉淀（测试通过 → git 工具库 → 三关审批） ----------------

def _extract_description(task: dict) -> str:
    """从能力题的 docstring 提取工具描述（第一行）。"""
    prompt = task.get("prompt") or ""
    for line in prompt.splitlines():
        s = line.strip().strip('"').strip()
        if s and not s.startswith("def ") and not s.startswith("from ") \
           and not s.startswith("import ") and not s.startswith(">>>"):
            return s[:200]
    return task.get("entry_point", "")


def promote_to_tool(conn, run_id, tool_name="", description="",
                    input_schema=None) -> dict:
    """把一次测试通过的 run 沉淀为 MCP 工具：写 git 工具库 + commit + 落库 pending。

    铁律：测试通过只是提名，工具仍需三关校验 + 用户审批才能被数字人调用。"""
    import json as _json
    run = conn.execute("SELECT * FROM capability_runs WHERE id=?",
                       (run_id,)).fetchone()
    if not run:
        return {"ok": False, "error": "run not found"}
    if run["verdict"] != "pass":
        return {"ok": False, "error": f"run not passed (verdict={run['verdict']})"}
    task = get_task(conn, run["task_id"])
    if not task:
        return {"ok": False, "error": "task not found"}

    name = (tool_name or task["entry_point"]).strip()
    desc = (description or _extract_description(task)).strip()
    schema = input_schema or {"type": "object", "properties": {}}

    from . import mcp_repo
    res = mcp_repo.add_tool(
        name, run["code"], description=desc,
        entry_point=task["entry_point"], input_schema=schema,
        commit_note=f"add tool {name} (from {task['task_key']}, run {run_id})")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}

    cur = conn.execute(
        "INSERT INTO capability_tools(run_id, task_id, tool_name, description,"
        " entry_point, input_schema, code, git_hash, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,'pending',?)",
        (run_id, task["id"], name, desc, task["entry_point"],
         _json.dumps(schema, ensure_ascii=False), run["code"],
         res.get("hash"), db.now()))
    conn.commit()
    return {"ok": True, "tool_id": cur.lastrowid,
            "tool_name": name, "git_hash": res.get("hash")}


def list_tools(conn, status=None) -> list[dict]:
    """沉淀工具列表（可按状态过滤）。"""
    sql = ("SELECT id, tool_name, description, entry_point, git_hash, status,"
           " created_at FROM capability_tools")
    params = []
    if status:
        sql += " WHERE status=?"
        params = [status]
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def set_tool_status(conn, tool_id, status) -> dict:
    """审批工具：approved / rejected。approved 后才可被数字人调用。"""
    if status not in ("approved", "rejected"):
        return {"ok": False, "error": "status must be approved/rejected"}
    r = conn.execute("SELECT * FROM capability_tools WHERE id=?",
                     (tool_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "tool not found"}
    conn.execute("UPDATE capability_tools SET status=? WHERE id=?",
                 (status, tool_id))
    conn.commit()
    return {"ok": True, "tool_id": tool_id, "tool_name": r["tool_name"],
            "status": status}


def repo_log(limit=20) -> dict:
    """工具库 git 版本历史。"""
    from . import mcp_repo
    return {"head": mcp_repo.head(), "commits": mcp_repo.log(limit)}


def repo_rollback(commit_hash) -> dict:
    """回滚工具库到指定 commit（危险操作）。"""
    from . import mcp_repo
    return mcp_repo.rollback(commit_hash)
