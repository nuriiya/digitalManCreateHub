# -*- coding: utf-8 -*-
"""Domain tags: normalize + structure gate + graph payload."""
from app import ontology


def test_normalize_tags_dedupe_strip_cap():
    assert ontology._normalize_tags([" 规则 ", "规则", "法律"]) == ["规则", "法律"]
    assert ontology._normalize_tags(["a", "b", "c", "d", "e", "f"]) == ["a", "b", "c", "d", "e"]
    assert ontology._normalize_tags(["x" * 20, "ok"]) == ["ok"]
    assert ontology._normalize_tags(["", "  ", None, "ok"]) == ["ok"]
    assert ontology._normalize_tags("not-a-list") == []
    assert ontology._normalize_tags(None) == []


def test_structure_gate_accepts_valid_tags():
    ok, _ = ontology.validate_structure({"name": "报销", "type": "规则",
                                          "tags": ["规则", "法律"]})
    assert ok


def test_structure_gate_rejects_bad_tags():
    # too many
    ok, _ = ontology.validate_structure({"name": "x", "tags": ["1", "2", "3", "4", "5", "6"]})
    assert not ok
    # duplicate
    ok, _ = ontology.validate_structure({"name": "x", "tags": ["规则", "规则"]})
    assert not ok
    # non-string member
    ok, _ = ontology.validate_structure({"name": "x", "tags": [1]})
    assert not ok
    # non-list
    ok, _ = ontology.validate_structure({"name": "x", "tags": "规则"})
    assert not ok


def test_graph_returns_tags(env):
    cur = env.execute(
        "INSERT INTO candidates(kind, name, definition, status, tags, created_at)"
        " VALUES('entity', '报销流程', '员工报销', 'pending', ?, 1.0)",
        (["规则", "法律"],))
    env.commit()
    g = ontology.graph(env)
    node = next(n for n in g["nodes"] if n["name"] == "报销流程")
    assert node["tags"] == ["规则", "法律"]


def test_tags_pool_endpoint_query(env):
    env.execute(
        "INSERT INTO candidates(kind, name, status, tags, created_at)"
        " VALUES('entity', 'a', 'pending', ?, 1.0),"
        " ('entity', 'b', 'pending', ?, 1.0)", (["规则"], ["规则", "法律"]))
    env.commit()
    rows = env.execute(
        "SELECT t, COUNT(*) c FROM (SELECT unnest(tags) t FROM candidates)"
        " x GROUP BY t ORDER BY c DESC, t").fetchall()
    by_tag = {r["t"]: r["c"] for r in rows}
    assert by_tag["规则"] == 2
    assert by_tag["法律"] == 1
