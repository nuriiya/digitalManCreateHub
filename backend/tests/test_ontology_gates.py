# -*- coding: utf-8 -*-
"""Ontology gate tests: structure / evidence / dedupe - the deterministic
half of nominate-vs-adjudicate (LLM never gets the final say)."""


def test_structure_gate_type_closed_set():
    from app.ontology import validate_structure, ENTITY_TYPES
    ok, _ = validate_structure({"name": "报销系统", "type": "系统",
                                "definition": "d", "mentions": ["报销系统"]})
    assert ok
    ok, reason = validate_structure({"name": "异常类型", "type": "不存在的类型"})
    assert not ok and "closed set" in reason


def test_structure_gate_lengths_and_fields():
    from app.ontology import validate_structure
    assert not validate_structure({"name": ""})[0]
    assert not validate_structure({"name": "超" * 40})[0]
    assert not validate_structure({"name": "ok", "definition": "d" * 400})[0]
    assert not validate_structure({"name": "ok", "mentions": []})[0]
    assert not validate_structure({"name": "ok", "mentions": ["  "]})[0]
    assert not validate_structure("not an object")[0]


def test_evidence_gate_literal_match():
    from app.ontology import locate_mention
    text = "《报销系统》是公司财务流程的核心系统。"
    span = locate_mention("报销系统", text)
    assert span == (1, 5)
    assert text[span[0]:span[1]] == "报销系统"


def test_evidence_gate_fabricated_mention_rejected():
    from app.ontology import locate_mention
    text = "《报销系统》是公司财务流程的核心系统。"
    # paraphrased / translated / invented mentions must fail
    assert locate_mention("报销流程系统", text) is None
    assert locate_mention("reimbursement system", text) is None
    assert locate_mention("", text) is None


def test_dedupe_gate(env):
    from app import ontology
    conn = env
    conn.execute("INSERT INTO candidates(kind, name, status, created_at)"
                 " VALUES('entity', '报销系统', 'pending', 0)")
    conn.commit()
    dup = ontology.find_duplicate(conn, "entity", " 报销系统 ")
    assert dup is not None and dup["name"] == "报销系统"
    assert ontology.find_duplicate(conn, "entity", "审批系统") is None
