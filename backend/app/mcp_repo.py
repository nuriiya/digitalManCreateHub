# -*- coding: utf-8 -*-
"""MCP 工具库：git 版本管理的工具代码仓库。

能力测试通过的工具 → 写代码文件 → git commit（版本可追溯/回滚）。
每个能力改动一个 commit，符合「版本管理意识」铁律。

目录结构：
    mcp_tools/
        .git/                 git 仓库
        <tool_name>.py        工具代码（docstring = 描述，含 input_schema 注释）
        manifest.json         工具清单（name -> description/entry_point/schema/file）
"""
import json
import subprocess
from pathlib import Path

from . import db

REPO_DIR = Path(__file__).resolve().parent.parent / "mcp_tools"
MANIFEST = REPO_DIR / "manifest.json"


def _git(args, cwd=REPO_DIR, timeout=30):
    try:
        r = subprocess.run(["git", *args], cwd=cwd,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except FileNotFoundError:
        return False, "git not found"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def ensure_repo() -> bool:
    """确保 git 仓库存在（首次 init + 配置身份）。"""
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not (REPO_DIR / ".git").exists():
        _git(["init"])
        _git(["config", "user.name", "rag-mcp"])
        _git(["config", "user.email", "rag-mcp@local"])
    return True


def _read_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def add_tool(name: str, code: str, description: str = "",
             entry_point: str = "", input_schema: dict = None,
             commit_note: str = "") -> dict:
    """添加/更新一个工具：写 .py 文件 + 更新 manifest + git commit。

    返回 {ok, path, hash}。内容未变时 commit 会被 git 跳过（hash 仍返回）。"""
    ensure_repo()
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    # 工具代码文件：docstring 描述 + input_schema 注释 + 代码
    desc = (description or name).replace("\n", " ").strip()
    header = f'"""{desc}"""\n'
    if input_schema:
        header += (f"# input_schema: "
                   f"{json.dumps(input_schema, ensure_ascii=False)}\n")
    fpath = REPO_DIR / f"{name}.py"
    fpath.write_text(header + "\n" + code, encoding="utf-8")

    # manifest
    m = _read_manifest()
    m[name] = {
        "description": desc,
        "entry_point": entry_point or name,
        "input_schema": input_schema or {},
        "file": f"{name}.py",
    }
    _write_manifest(m)

    _git(["add", f"{name}.py", "manifest.json"])
    note = commit_note or f"add tool {name}"
    _git(["commit", "-m", note])
    _, hash_ = _git(["rev-parse", "HEAD"])
    return {"ok": True, "path": str(fpath), "hash": hash_}


def remove_tool(name: str, commit_note: str = "") -> dict:
    """删除工具：删文件 + 更新 manifest + git commit。"""
    ensure_repo()
    fpath = REPO_DIR / f"{name}.py"
    m = _read_manifest()
    if name in m:
        del m[name]
        _write_manifest(m)
    if fpath.exists():
        _git(["rm", "-f", f"{name}.py"])
    _git(["add", "manifest.json"])
    _git(["commit", "-m", commit_note or f"remove tool {name}"])
    _, hash_ = _git(["rev-parse", "HEAD"])
    return {"ok": True, "hash": hash_}


def log(limit=20) -> list[dict]:
    """git 历史（工具库版本管理视图）。"""
    ok, out = _git(["log", f"-{limit}", "--pretty=format:%H|%s"])
    if not ok:
        return []
    entries = []
    for line in out.splitlines():
        if "|" in line:
            h, s = line.split("|", 1)
            entries.append({"hash": h, "subject": s})
    return entries


def rollback(commit_hash: str) -> dict:
    """回滚到指定 commit（危险操作，仅供用户显式调用）。"""
    ok, out = _git(["reset", "--hard", commit_hash])
    if not ok:
        return {"ok": False, "error": out}
    _, h = _git(["rev-parse", "HEAD"])
    return {"ok": True, "hash": h}


def head() -> str | None:
    """当前 HEAD commit。"""
    ok, h = _git(["rev-parse", "HEAD"])
    return h if ok else None
