# -*- coding: utf-8 -*-
"""MCP sandbox registry: Docker-isolated MCP servers.

Each registered MCP server is a Docker container (per-user isolation). The
registry stores config in the mcp_servers table and drives lifecycle via the
host's `wsl -e docker` CLI (Docker lives in WSL2 on this dev machine). Real
status is re-read from the container on every list; the stored `status` column
is a fallback only.

Lifecycle contract:
  - start: docker run -d (name = rag_mcp_<id>, optional -p port, image + cmd)
  - stop:  docker stop + rm -f
  - status: docker inspect (State.Status), else 'stopped'

This is the tool-library substrate (N7 in the design docs): sandboxed candidate
tools still must pass the three-gate + approval flow before a digital persona
can call them.
"""
import shlex
import subprocess

from . import db


def _docker(args, timeout=120):
    """Run a docker command.

    Containerized backend talks to the host daemon via the mounted
    /var/run/docker.sock (direct `docker` CLI). On the legacy WSL2 dev setup
    (no local CLI) it falls back to `wsl.exe -e docker`.
    """
    # 1) direct docker CLI (container with docker.sock mount, or host CLI)
    try:
        r = subprocess.run(
            ["docker", *args],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except FileNotFoundError:
        pass  # no docker CLI -> fall back to WSL2
    # 2) WSL2 docker (legacy dev machine without a local docker CLI)
    try:
        r = subprocess.run(
            ["wsl.exe", "-e", "docker", *args],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


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
    row = conn.execute("SELECT * FROM mcp_servers WHERE id=?",
                       (mcp_id,)).fetchone()
    if not row:
        return False, "not found"
    if not row["image"]:
        return False, "no image configured"
    name = _container_name(mcp_id)
    _docker(["rm", "-f", name])  # drop any stale container
    args = ["run", "-d", "--name", name]
    if row["port"]:
        args += ["-p", f"{row['port']}:{row['port']}"]
    args.append(row["image"])
    if row["command"]:
        args += shlex.split(row["command"])
    ok, out = _docker(args)
    if ok:
        conn.execute("UPDATE mcp_servers SET status='running' WHERE id=?",
                     (mcp_id,))
        conn.commit()
        return True, name
    conn.execute("UPDATE mcp_servers SET status='error' WHERE id=?", (mcp_id,))
    conn.commit()
    return False, out


def stop_server(conn, mcp_id) -> bool:
    name = _container_name(mcp_id)
    _docker(["stop", name])
    _docker(["rm", "-f", name])
    conn.execute("UPDATE mcp_servers SET status='stopped' WHERE id=?", (mcp_id,))
    conn.commit()
    return True


def get_status(mcp_id) -> str:
    name = _container_name(mcp_id)
    ok, out = _docker(["inspect", "-f", "{{.State.Status}}", name])
    return out if ok and out else "stopped"
