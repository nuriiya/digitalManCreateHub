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
import shlex

from . import db


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
    # drop any stale container
    try:
        client.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass
    ports = {}
    if row["port"]:
        ports[f"{row['port']}/tcp"] = row["port"]
    command = shlex.split(row["command"]) if row["command"] else None
    try:
        client.containers.run(
            row["image"], command=command, name=name,
            detach=True, ports=ports)
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
