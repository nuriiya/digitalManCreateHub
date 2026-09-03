# -*- coding: utf-8 -*-
"""DB-level disk-protection regressions (issue: 100% disk during jobs).

Root cause measured 2026-08-31: the RAG process itself wrote ~0.4MB/30s, but
per-commit fsyncs + Windows Search re-crawling the fast-changing WAL pushed
SearchIndexer to 3.5GB reads/30s. Protections: synchronous=NORMAL + 'not
content indexed' file attribute on the data files.
"""


def test_wal_synchronous_normal(env):
    """WAL + NORMAL: commits skip fsync (app-crash safe, power-cut tolerant)."""
    mode = env.execute("PRAGMA synchronous").fetchone()[0]
    assert mode == 1  # 0=OFF 1=NORMAL 2=FULL


def test_wal_mode_still_on(env):
    assert env.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_not_indexed_marking_is_silent_and_idempotent(env, tmp_path):
    """_protect_from_indexer must never raise nor corrupt the file, on any
    platform (non-Windows is a no-op, Windows marks +0x2000)."""
    from app import db
    f = tmp_path / "some.db"
    f.write_bytes(b"payload")
    db._protect_from_indexer(f)   # first call
    db._protect_from_indexer(f)   # idempotent
    assert f.read_bytes() == b"payload"


def test_not_indexed_attribute_set_on_windows(env, tmp_path):
    """On Windows the FILE_ATTRIBUTE_NOT_CONTENT_INDEXED bit must actually
    land on the db file (this is the SearchIndexer crawl guard)."""
    import sys
    if sys.platform != "win32":
        return  # no-op off Windows by design
    import ctypes
    from app import db
    f = tmp_path / "some.db"
    f.write_bytes(b"payload")
    db._protect_from_indexer(f)
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(f))
    assert attrs != 0xFFFFFFFF
    assert attrs & 0x2000  # FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
