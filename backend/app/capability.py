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
