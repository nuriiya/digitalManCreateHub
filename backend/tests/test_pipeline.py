# -*- coding: utf-8 -*-
"""pipeline 编排数据模型 + 校验器测试（数据层，env fixture 隔离 schema）。"""
from app import pipeline


def _mk_pipeline(conn, name="调研X并写demo"):
    return pipeline.create_pipeline(conn, name, "desc", ["调研", "demo"])


def test_create_and_get_pipeline(env):
    pid = _mk_pipeline(env)
    assert pid is not None
    p = pipeline.get_pipeline(env, pid)
    assert p["name"] == "调研X并写demo"
    assert p["tags"] == ["调研", "demo"]
    assert p["status"] == "draft"
    assert p["nodes"] == []
    assert p["relations"] == []


def test_add_nodes_and_relations(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1", None, pipeline.KIND_NOMINATE, "流程设计师")
    n2 = pipeline.add_node(env, pid, "n2", None, pipeline.KIND_DETERMINISTIC, "建数字人")
    assert n1 and n2
    r = pipeline.add_relation(env, pid, n1, n2, pipeline.RELATION_DESIGN,
                              "领域文献", "doc_refs[]")
    assert r is not None
    p = pipeline.get_pipeline(env, pid)
    assert len(p["nodes"]) == 2
    assert len(p["relations"]) == 1
    assert p["relations"][0]["relation_type"] == pipeline.RELATION_DESIGN
    assert p["relations"][0]["handoff_type"] == "领域文献"


def test_validate_clean(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1", None, "nominate", "a")
    n2 = pipeline.add_node(env, pid, "n2", None, "deterministic", "b")
    pipeline.add_relation(env, pid, n1, n2, "design")
    assert pipeline.validate_pipeline(env, pid) == []


def test_validate_cycle(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    pipeline.add_relation(env, pid, n1, n2, "handoff")
    pipeline.add_relation(env, pid, n2, n1, "handoff")
    errors = pipeline.validate_pipeline(env, pid)
    assert any("环" in e for e in errors)


def test_validate_dangling_entry(env):
    pid = _mk_pipeline(env)
    pipeline.add_node(env, pid, "n1")
    pipeline.update_pipeline(env, pid, {"entry_node_id": 99999})  # 悬空
    errors = pipeline.validate_pipeline(env, pid)
    assert any("entry_node" in e for e in errors)


def test_add_relation_rejects_bad_type(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    assert pipeline.add_relation(env, pid, n1, n2, "not_a_type") is None  # 闭集
    assert pipeline.add_relation(env, pid, n1, n1, "handoff") is None      # 自环


def test_add_node_rejects_bad_kind(env):
    pid = _mk_pipeline(env)
    assert pipeline.add_node(env, pid, "n1", None, "weird") is None
    assert pipeline.add_node(env, pid, "", None, "nominate") is None


def test_approve_sets_status_and_tags(env):
    pid = _mk_pipeline(env)
    assert pipeline.approve_pipeline(env, pid)
    p = pipeline.get_pipeline(env, pid)
    assert p["status"] == "approved"
    assert p["tags"] == ["调研", "demo"]


def test_changes_nominate_approve_merge(env):
    pid = _mk_pipeline(env)
    cid = pipeline.nominate_changes(env, pid, [
        {"action": "add_node",
         "payload": {"node_key": "n3", "kind": "nominate", "step_name": "代码复核"},
         "reason": "加复核节点"},
    ])
    assert cid is not None
    changes = pipeline.list_changes(env, pid)
    assert len(changes) == 1 and changes[0]["status"] == "pending"
    assert pipeline.apply_change(env, cid)
    p = pipeline.get_pipeline(env, pid)
    assert any(n["node_key"] == "n3" for n in p["nodes"])
    assert pipeline.list_changes(env, pid)[0]["status"] == "approved"


def test_change_reject_does_not_apply(env):
    pid = _mk_pipeline(env)
    cid = pipeline.nominate_changes(env, pid, [
        {"action": "set_tags", "payload": {"tags": ["x"]}},
    ])
    assert pipeline.reject_change(env, cid)
    assert pipeline.list_changes(env, pid)[0]["status"] == "rejected"
    assert pipeline.get_pipeline(env, pid)["tags"] == ["调研", "demo"]


def test_change_rejects_bad_action(env):
    pid = _mk_pipeline(env)
    # 越界 action 直接丢弃，返回 None
    assert pipeline.nominate_changes(env, pid, [{"action": "hack", "payload": {}}]) is None
    assert pipeline.list_changes(env, pid) == []


def test_remove_node_cascades_relations(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    pipeline.add_relation(env, pid, n1, n2, "handoff")
    pipeline.remove_node(env, n1)
    p = pipeline.get_pipeline(env, pid)
    assert len(p["nodes"]) == 1
    assert len(p["relations"]) == 0  # 级联删除


def test_change_add_relation_by_node_key(env):
    """LLM 提名 add_relation 时常用 node_key（字符串）而非数字 id，apply 要能解析。"""
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    cid = pipeline.nominate_changes(env, pid, [
        {"action": "add_relation",
         "payload": {"from_node_key": "n1", "to_node_key": "n2",
                     "relation_type": "handoff", "handoff_type": "产出",
                     "handoff_schema": "x"},
         "reason": "LLM 用 node_key 提名"},
    ])
    assert pipeline.apply_change(env, cid)
    p = pipeline.get_pipeline(env, pid)
    assert len(p["relations"]) == 1
    assert p["relations"][0]["from_node_id"] == n1
    assert p["relations"][0]["to_node_id"] == n2


def test_update_node(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1", None, "nominate", "a")
    assert pipeline.update_node(env, n1, {"step_name": "改名", "kind": "deterministic"})
    p = pipeline.get_pipeline(env, pid)
    n = p["nodes"][0]
    assert n["step_name"] == "改名"
    assert n["kind"] == "deterministic"


# ---------------- 执行引擎 ----------------

def test_topo_sort_linear(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    n3 = pipeline.add_node(env, pid, "n3")
    pipeline.add_relation(env, pid, n1, n2, "handoff")
    pipeline.add_relation(env, pid, n2, n3, "handoff")
    p = pipeline.get_pipeline(env, pid)
    order = pipeline.topo_sort(p["nodes"], p["relations"])
    assert [n["node_key"] for n in order] == ["n1", "n2", "n3"]


def test_topo_sort_branch(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    n3 = pipeline.add_node(env, pid, "n3")
    n4 = pipeline.add_node(env, pid, "n4")
    pipeline.add_relation(env, pid, n1, n2, "handoff")
    pipeline.add_relation(env, pid, n1, n3, "handoff")
    pipeline.add_relation(env, pid, n2, n4, "handoff")
    pipeline.add_relation(env, pid, n3, n4, "handoff")
    p = pipeline.get_pipeline(env, pid)
    order = pipeline.topo_sort(p["nodes"], p["relations"])
    keys = [n["node_key"] for n in order]
    assert keys[0] == "n1"
    assert keys[-1] == "n4"
    assert set(keys[1:3]) == {"n2", "n3"}


def test_create_run_and_handoff(env):
    pid = _mk_pipeline(env)
    n1 = pipeline.add_node(env, pid, "n1")
    n2 = pipeline.add_node(env, pid, "n2")
    pipeline.add_relation(env, pid, n1, n2, "handoff")
    run_id = pipeline.create_run(env, pid, job_id=None)
    assert run_id is not None
    pipeline.store_handoff(env, run_id, n1, "上游产出")
    p = pipeline.get_pipeline(env, pid)
    node2 = [n for n in p["nodes"] if n["node_key"] == "n2"][0]
    inputs = pipeline.collect_inputs(env, run_id, node2, p["nodes"], p["relations"])
    assert inputs == ["上游产出"]


def test_run_deterministic_unimplemented(env):
    out = pipeline._run_deterministic(env, {"step_name": "查表计数"}, [], 0)
    assert "尚未实现" in out


def test_run_deterministic_build_no_workdir(env):
    out = pipeline._run_build_persona(env, 0)
    assert "work_dir" in out  # settings 沙盒化后 work_dir 为空 → 返回 error


def test_run_nominate_no_persona(env):
    out = pipeline._run_nominate(env, {"persona_id": None, "step_name": "x", "node_key": "n1"}, [])
    assert "未绑定数字人" in out

