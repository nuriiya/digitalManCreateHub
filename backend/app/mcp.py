# -*- coding: utf-8 -*-
"""MCP sandbox registry: Docker-isolated MCP servers.

Each registered MCP server is a Docker container (per-user isolation). The
registry stores config in the mcp_servers table and drives lifecycle through
the host docker daemon via the mounted /var/run/docker.sock (docker-py SDK,
no docker CLI needed). Real status is re-read from the container on every
list; the stored `status` column is a fallback only.

Lifecycle contract:
  - start: containers.run(detach=True, name = rag_mcp_<id>, image + cmd + ports)
  - stop:  container.stop() + remove(force=True)
  - status: container.status, else 'stopped'

This is the tool-library substrate (N7 in the design docs): sandboxed candidate
tools still must pass the three-gate + approval flow before a digital persona
can call them.
"""
import json
import os
import shlex
from pathlib import Path

from . import db

# MCP 定义导入目录：模型（数字人）把 MCP 定义 JSON 写到这个目录，后端扫描后
# 自动导入 mcp_servers 表（提名-裁决分离：导入后 approval_status=pending，用户
# 审批后才 approved）。项目根下的 mcp_imports/，挂载进容器。
# mcp.py 位于 backend/app/mcp.py，项目根 = parent.parent.parent。
_ROOT = Path(__file__).resolve().parent.parent.parent
IMPORT_DIR = _ROOT / "mcp_imports"

MCP_TRANSPORTS = ("stdio", "http")


def _client():
    """docker-py client over the mounted /var/run/docker.sock."""
    import docker
    return docker.from_env()


def _container_name(mcp_id) -> str:
    return f"rag_mcp_{mcp_id}"


def list_servers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM mcp_servers ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["status"] = get_status(r["id"])  # live status wins
        out.append(d)
    return out


def create_server(conn, name, description="", transport="http",
                  image="", command="", port=0) -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "name is required"
    if not image:
        return False, "image is required"
    try:
        cur = conn.execute(
            "INSERT INTO mcp_servers(name, description, transport, image,"
            " command, port, status, created_at)"
            " VALUES(?,?,?,?,?,?, 'stopped', ?)",
            (name, description, transport, image, command, port or 0, db.now()))
        conn.commit()
        return True, str(cur.lastrowid)
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def delete_server(conn, mcp_id) -> bool:
    stop_server(conn, mcp_id)
    conn.execute("DELETE FROM mcp_servers WHERE id=?", (mcp_id,))
    conn.commit()
    return True


def start_server(conn, mcp_id) -> tuple[bool, str]:
    import docker
    row = conn.execute("SELECT * FROM mcp_servers WHERE id=?",
                       (mcp_id,)).fetchone()
    if not row:
        return False, "not found"
    if not row["image"]:
        return False, "no image configured"
    name = _container_name(mcp_id)
    client = _client()
    # 确保镜像存在：本地有就用；有 build 配置则自动构建（基础镜像走国内源）；
    # 否则国内源前缀拉取。绝不让 docker-py 自动 pull 走失效的 daocloud。
    ok, msg = _ensure_image(client, row["image"], row.get("build"))
    if not ok:
        conn.execute("UPDATE mcp_servers SET status='error' WHERE id=?",
                     (mcp_id,))
        conn.commit()
        return False, msg
    # drop any stale container
    try:
        client.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass
    ports = {}
    if row["port"]:
        ports[f"{row['port']}/tcp"] = row["port"]
    command = shlex.split(row["command"]) if row["command"] else None
    # stdio 传输的 MCP server 需要保持 stdin 打开（否则 mcp.run() 检测到 stdin
    # 关闭立即退出）。http 传输则无需 stdin。
    stdin_open = (row["transport"] or "http") == "stdio"
    try:
        client.containers.run(
            row["image"], command=command, name=name,
            detach=True, ports=ports, stdin_open=stdin_open)
    except Exception as e:  # noqa: BLE001
        conn.execute("UPDATE mcp_servers SET status='error' WHERE id=?",
                     (mcp_id,))
        conn.commit()
        return False, str(e)
    conn.execute("UPDATE mcp_servers SET status='running' WHERE id=?",
                 (mcp_id,))
    conn.commit()
    return True, name


def stop_server(conn, mcp_id) -> bool:
    import docker
    name = _container_name(mcp_id)
    try:
        c = _client().containers.get(name)
        c.stop()
        c.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception:  # noqa: BLE001
        pass
    conn.execute("UPDATE mcp_servers SET status='stopped' WHERE id=?", (mcp_id,))
    conn.commit()
    return True


def get_status(mcp_id) -> str:
    import docker
    name = _container_name(mcp_id)
    try:
        return _client().containers.get(name).status
    except docker.errors.NotFound:
        return "stopped"
    except Exception:  # noqa: BLE001
        return "stopped"


# ---------------- JSON 定义导入（模型输出 JSON → 自动导入） ----------------
# 模型（数字人）通过 write_file 动作把 MCP 定义 JSON 写到 IMPORT_DIR，后端扫描
# 该目录，逐文件校验 + 幂等 upsert 到 mcp_servers。这是「提名-裁决分离」的入口：
# 模型只提名（写 JSON），确定性代码裁决（校验 schema + 闭集），用户终审（审批）。

def validate_mcp_def(data: dict) -> tuple[bool, str]:
    """确定性校验 MCP 定义 JSON（结构 + 闭集 + 类型）。返回 (ok, error)。"""
    if not isinstance(data, dict):
        return False, "定义必须是 JSON 对象"
    name = (data.get("name") or "").strip()
    if not name:
        return False, "name 必填"
    if len(name) > 128:
        return False, "name 过长（>128）"
    image = (data.get("image") or "").strip()
    if not image:
        return False, "image 必填（Docker 镜像）"
    transport = (data.get("transport") or "stdio").strip()
    if transport not in MCP_TRANSPORTS:
        return False, f"transport 必须是 {MCP_TRANSPORTS} 之一"
    port = data.get("port") or 0
    if not isinstance(port, int) or port < 0:
        return False, "port 必须是非负整数"
    # tools 闭集校验
    tools = data.get("tools") or []
    if not isinstance(tools, list):
        return False, "tools 必须是数组"
    for t in tools:
        if not isinstance(t, dict):
            return False, "tools 每项必须是对象"
        tn = (t.get("name") or "").strip()
        if not tn:
            return False, "tool 缺少 name"
        if "input_schema" in t and not isinstance(t["input_schema"], dict):
            return False, f"tool {tn} 的 input_schema 必须是对象"
    # build 可选：{context, dockerfile}
    build = data.get("build")
    if build is not None:
        if not isinstance(build, dict):
            return False, "build 必须是对象"
        if not (build.get("context") or "").strip():
            return False, "build.context 必填"
    return True, ""


def import_from_json(conn, data: dict, source_path: str = "") -> tuple[bool, str, int]:
    """校验 + 幂等导入一个 MCP 定义（按 name upsert）。返回 (ok, msg, server_id)。

    模型提名 → approval_status=pending；同名已存在则更新其内容（保留 id 与审批态）。
    """
    ok, err = validate_mcp_def(data)
    if not ok:
        return False, err, 0
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    transport = (data.get("transport") or "stdio").strip()
    image = (data.get("image") or "").strip()
    command = (data.get("command") or "").strip()
    port = int(data.get("port") or 0)
    tools = data.get("tools") or []
    build = data.get("build")
    tools_json = json.dumps(tools, ensure_ascii=False)
    build_json = json.dumps(build, ensure_ascii=False) if build is not None else None
    existing = conn.execute("SELECT id FROM mcp_servers WHERE name=?",
                            (name,)).fetchone()
    try:
        if existing:
            conn.execute(
                "UPDATE mcp_servers SET description=?, transport=?, image=?,"
                " command=?, port=?, tools=?, build=?, source_path=?"
                " WHERE id=?",
                (description, transport, image, command, port, tools_json,
                 build_json, source_path, existing["id"]))
            conn.commit()
            return True, "updated", existing["id"]
        cur = conn.execute(
            "INSERT INTO mcp_servers(name, description, transport, image, command,"
            " port, status, approval_status, tools, build, source_path, created_at)"
            " VALUES(?,?,?,?,?,?, 'stopped', 'pending', ?, ?, ?, ?)",
            (name, description, transport, image, command, port,
             tools_json, build_json, source_path, db.now()))
        conn.commit()
        return True, "created", cur.lastrowid
    except Exception as e:  # noqa: BLE001
        return False, str(e), 0


def scan_import_dir(conn) -> dict:
    """扫描 IMPORT_DIR 里的 *.json，逐个导入。返回导入结果清单。"""
    results = []
    if not IMPORT_DIR.is_dir():
        return {"scanned": 0, "results": results, "dir": str(IMPORT_DIR)}
    files = sorted(IMPORT_DIR.glob("*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            results.append({"file": f.name, "ok": False,
                            "error": f"JSON 解析失败: {e}"})
            continue
        ok, msg, sid = import_from_json(conn, data, source_path=str(f))
        results.append({"file": f.name, "ok": ok, "msg": msg, "server_id": sid})
    return {"scanned": len(files), "results": results, "dir": str(IMPORT_DIR)}


def approve_server(conn, mcp_id: int, approve: bool) -> tuple[bool, str]:
    """用户终审：approve=True → approved，False → rejected。"""
    row = conn.execute("SELECT id FROM mcp_servers WHERE id=?",
                       (mcp_id,)).fetchone()
    if not row:
        return False, "not found"
    status = "approved" if approve else "rejected"
    conn.execute("UPDATE mcp_servers SET approval_status=? WHERE id=?",
                 (status, mcp_id))
    conn.commit()
    return True, status


# ---------------- 镜像准备（避免 docker-py 自动 pull 走失效的 daocloud） ----------------
# Docker Desktop 的 daemon registry-mirrors 指向已失效的 daocloud，任何镜像拉取都会
# EOF。故 start_server 前显式准备镜像：本地有→用；有 build→自动构建（基础镜像走国内
# 源前缀预拉）；否则国内源前缀拉取。绝不依赖 docker-py 的隐式 auto-pull。

# 国内镜像源（与 start1.ps1 / .env 默认源一致，dockerproxy.net 对大镜像更稳）
CN_MIRRORS = ("dockerproxy.net", "docker.1ms.run", "docker.xuanyuan.me")


def _library_image(image: str) -> str:
    """官方镜像 → library 命名空间名（python:3.12 → library/python:3.12）。"""
    name = image
    for prefix in ("docker.io/", "registry-1.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name.startswith("library/"):
        return name
    return f"library/{name}"


def _pull_official(client, image: str) -> tuple[bool, str]:
    """用国内源拉取官方镜像（library 命名空间）并 tag 回标准名。"""
    lib = _library_image(image)
    for prefix in CN_MIRRORS:
        try:
            client.images.pull(f"{prefix}/{lib}")
            client.images.get(f"{prefix}/{lib}").tag(image)
            return True, f"via {prefix}"
        except Exception:  # noqa: BLE001
            continue
    return False, f"所有国内源拉取 {image} 失败"


def _prepull_base_images(client, dockerfile_path: Path) -> list[str]:
    """解析 Dockerfile 的 FROM 行，用国内源预拉官方基础镜像（避免 build 时走 daocloud）。"""
    pulled = []
    if not dockerfile_path.exists():
        return pulled
    for line in dockerfile_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.upper().startswith("FROM "):
            img = s[5:].strip().split()[0]
            ok, msg = _pull_official(client, img)
            pulled.append(f"{img} ({'ok' if ok else msg})")
    return pulled


def _ensure_image(client, image: str, build: dict | None) -> tuple[bool, str]:
    """确保镜像存在：本地有→用；有 build→自动构建；否则国内源拉取。"""
    import docker
    try:
        client.images.get(image)
        return True, ""
    except docker.errors.ImageNotFound:
        pass
    # 有 build 配置 → 自动构建（context 相对项目根，容器内即 _ROOT 下）
    if build and (build.get("context") or "").strip():
        context = _ROOT / build["context"]
        dockerfile = build.get("dockerfile") or "Dockerfile"
        df_path = context / dockerfile
        if not df_path.exists():
            return False, f"构建上下文缺失：{df_path}（sandbox 目录需挂载进容器）"
        _prepull_base_images(client, df_path)
        try:
            client.images.build(path=str(context), dockerfile=dockerfile,
                                tag=image, rm=True)
            return True, f"built from {build['context']}"
        except Exception as e:  # noqa: BLE001
            return False, f"build 失败：{str(e)[:200]}"
    # 无 build → 国内源前缀拉取
    for prefix in CN_MIRRORS:
        try:
            client.images.pull(f"{prefix}/{image}")
            client.images.get(f"{prefix}/{image}").tag(image)
            return True, f"pulled via {prefix}"
        except Exception:  # noqa: BLE001
            continue
    return False, f"镜像 {image} 不存在（无 build 配置，且国内源拉取失败）"
