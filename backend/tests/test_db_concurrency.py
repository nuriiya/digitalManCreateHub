# -*- coding: utf-8 -*-
"""B 方案（线程本地连接）的并发安全回归：多线程各自拿独立连接，
并发写库不再死锁、不串扰。这是对 2026-09-05 ontology job #5 死锁的根治验证。

背景：旧实现用进程级单例连接，ontology 并发 worker + 请求线程共享一个
psycopg3 连接（非线程安全）-> DeadlockDetected + 跨线程事务污染。
"""
import threading

from app import db


def test_thread_local_distinct_connections():
    """每个线程 get_conn() 拿到独立连接；10 线程并发写不死锁。"""
    ids: dict[int, int] = {}
    errors: list[str] = []
    barrier = threading.Barrier(10)  # 让所有线程同时开跑，最大化锁竞争

    def worker(i: int) -> None:
        try:
            c = db.get_conn()
            ids[i] = id(c.raw)
            barrier.wait()
            # 并发写：每个线程写自己命名的 kv 键（不冲突但会争行锁）
            for _ in range(30):
                c.execute(
                    "INSERT INTO kv(k, v) VALUES(%s, %s)"
                    " ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                    (f"conc_test_{i}", '{"n": 1}'))
            c.commit()
            # 并发读
            for _ in range(30):
                c.execute("SELECT v FROM kv WHERE k=%s", (f"conc_test_{i}",))
        except Exception as e:  # noqa: BLE001
            errors.append(f"thread {i}: {type(e).__name__}: {e}")
        finally:
            db.reset_conn()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert errors == [], f"并发写出现错误: {errors}"
    assert len(set(ids.values())) == 10, (
        f"应拿到 10 个独立连接，实际 {len(set(ids.values()))} 个")


def test_aborted_transaction_recovers():
    """单线程内一条 SQL 失败后，后续操作自动 rollback 恢复，不再被污染。"""
    c = db.get_conn()
    try:
        # 故意让一条 SQL 失败（引用不存在的列），触发 InFailedSqlTransaction
        try:
            c.execute("SELECT nonexistent_column FROM kv")
        except Exception:
            pass  # 预期失败
        # 关键：失败后，下一条 SQL 应能正常执行（execute 内部已 rollback）
        c.execute("SELECT 1")
        assert True
    finally:
        db.reset_conn()


def test_reset_conn_reopens_fresh():
    """reset_conn 关闭当前线程连接；下次 get_conn 重新建连。"""
    c1 = db.get_conn()
    db.reset_conn()
    c2 = db.get_conn()
    try:
        assert c1 is not c2
    finally:
        db.reset_conn()
