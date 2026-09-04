# -*- coding: utf-8 -*-
"""chat_sessions（多会话历史）的 CRUD 回归：创建 / 列表 / 重命名 / 删除级联。"""
from app import chat, db


def _make_identity(conn, name="测试数字人") -> int:
    cur = conn.execute(
        "INSERT INTO identities(name, mission, keywords, prompt, status, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (name, "m", "[]", "", "approved", db.now()))
    conn.commit()
    return cur.lastrowid


def test_auto_title_truncates():
    assert chat._auto_title("hello world") == "hello world"
    assert chat._auto_title("a" * 30) == ("a" * 20) + "…"
    assert chat._auto_title("  ") == "新对话"


def test_session_lifecycle(env):
    conn = env
    iid = _make_identity(conn)

    # create
    s = chat.create_session(conn, iid, "会话A")
    assert s is not None and s["title"] == "会话A"
    sid = s["id"]

    # list
    sessions = chat.list_sessions(conn, iid)
    assert [x["id"] for x in sessions] == [sid]
    assert sessions[0]["message_count"] == 0

    # save messages into the session
    chat._save(conn, iid, "user", "你好", sid)
    chat._save(conn, iid, "assistant", "你好！", sid)
    assert chat.list_sessions(conn, iid)[0]["message_count"] == 2

    # messages scoped to session
    msgs = chat.list_messages(conn, iid, sid)
    assert len(msgs) == 2
    assert msgs[0]["session_id"] == sid

    # rename
    assert chat.rename_session(conn, sid, "改名后")
    assert chat.list_sessions(conn, iid)[0]["title"] == "改名后"
    assert not chat.rename_session(conn, sid, "  ")  # empty title rejected

    # history scoped to session
    hist = chat._history_messages(conn, iid, 10, sid)
    assert [h["role"] for h in hist] == ["user", "assistant"]

    # delete cascades messages
    assert chat.delete_session(conn, sid) == 2
    assert chat.list_sessions(conn, iid) == []
    assert chat.list_messages(conn, iid, sid) == []


def test_create_session_unknown_identity(env):
    assert chat.create_session(env, 99999, "x") is None


def test_answer_auto_creates_session(env, fake_llm):
    conn = env
    iid = _make_identity(conn, "顾问")
    router = fake_llm
    router["reply"] = lambda msgs: "这是回答"

    r = chat.answer(conn, iid, "第一个问题", use_ontology=False,
                    provider="llm", use_rag=False)
    assert r.get("ok"), r
    assert r.get("session_id") is not None
    # 会话标题来自首条消息
    sess = chat.list_sessions(conn, iid)
    assert len(sess) == 1
    assert sess[0]["title"].startswith("第一个问题")

    # 追加到同一会话
    r2 = chat.answer(conn, iid, "追问", use_ontology=False, provider="llm",
                     use_rag=False, session_id=r["session_id"])
    assert r2.get("ok")
    assert len(chat.list_messages(conn, iid, r["session_id"])) == 4


def test_answer_rolls_back_session_on_error(env, fake_llm):
    conn = env
    iid = _make_identity(conn, "顾问")
    # 空消息会失败，自动创建的会话应被回滚
    r = chat.answer(conn, iid, "   ", use_ontology=False, provider="llm")
    assert not r.get("ok")
    assert chat.list_sessions(conn, iid) == []
