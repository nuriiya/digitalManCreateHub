# -*- coding: utf-8 -*-
"""验证 pipeline 引擎的 DFMEA 机制（design §15.3 的 E1 / E2 / E4）。

覆盖：
  E4  FMEA 专用 kind（步名映射 + 角色入站白名单）
  E1  ask 边执行语义（`_ask_sources` 取可询问名单 + `ask_expert` 名单外拒绝）
  E2  review 门控（`_is_review_gate` + `_review_verdict` 的确定性判定）

用临时 pipeline 验证 ask 边，跑完即删。不调用 LLM（裁决在 LLM 之前发生）。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_pipeline_fmea.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import actions, context_mgr as cm, db, fmea, pipeline  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(("    PASS  " if cond else "    FAIL  ") + label + (" " + extra if extra else ""))
    if not cond:
        fails.append(label)


conn = db.get_conn()

# ---- E4 FMEA kind ----
print("[1] E4 FMEA 专用 kind")
cases = {"失效模式分析": cm.KIND_FAILURE_MODES, "评分取值": cm.KIND_SOD,
         "DFMEA 表": cm.KIND_DFMEA, "DFMEA 报告": cm.KIND_DFMEA,
         "FMEA 分析": cm.KIND_FAILURE_MODES}
bad = [s for s, w in cases.items() if cm.kind_for_step(s) != w]
check("步名 -> kind 映射", not bad, f"异常={bad or '无'}")
check("kind 闭集含 3 个 FMEA 项",
      all(k in cm.KINDS for k in (cm.KIND_FAILURE_MODES, cm.KIND_SOD,
                                  cm.KIND_DFMEA)))
eng = cm.allowed_kinds_for("DFMEA 工程师", list(cm.KINDS))
check("DFMEA 工程师能收 DFMEA 表", cm.KIND_DFMEA in eng)
check("DFMEA 工程师能收需求规格", cm.KIND_REQUIREMENT in eng)

# ---- E2 review 门控 ----
print("[2] E2 review 门控判定")
check("FAIL 判定", pipeline._review_verdict("[REVIEW:FAIL] 依据不足") == "FAIL")
check("PASS 判定", pipeline._review_verdict("[REVIEW:PASS] 全部可追溯") == "PASS")
check("含糊 -> UNKNOWN", pipeline._review_verdict("看起来还行") == "UNKNOWN")
check("标记落在截断窗口外 -> UNKNOWN",
      pipeline._review_verdict("x" * 500 + "[REVIEW:FAIL]") == "UNKNOWN")
node_rev = {"id": 2, "node_key": "rev"}
rels_rev = [{"relation_type": pipeline.RELATION_REVIEW, "to_node_id": 2,
             "from_node_id": 1}]
check("有 review 入边 = 复核门", pipeline._is_review_gate(node_rev, rels_rev))
check("无 review 入边 != 复核门",
      not pipeline._is_review_gate({"id": 3, "node_key": "x"}, rels_rev))

# ---- E1 ask 边 ----
print("[3] E1 ask 边执行语义")
eng_id = conn.execute("SELECT id FROM identities WHERE name='DFMEA 工程师'"
                      ).fetchone()["id"]
exp_id = conn.execute("SELECT id FROM identities WHERE name='射频硬件专家'"
                      ).fetchone()["id"]
other_id = conn.execute("SELECT id FROM identities WHERE name='嵌入式固件专家'"
                        ).fetchone()["id"]
pid = pipeline.create_pipeline(conn, "_tmp_ask_verify", "机制验证临时图")
n_exp = pipeline.add_node(conn, pid, "expert", exp_id, step_name="提供部件信息")
n_eng = pipeline.add_node(conn, pid, "engineer", eng_id, step_name="DFMEA 分析")
pipeline.add_relation(conn, pid, n_exp, n_eng, pipeline.RELATION_ASK)
# 双向兼容：LLM 可能把 ask 画成「提问方 → 被问方」，也可能画成反向；
# 引擎按「另一端是否为本节点拓扑上游」判定，两种方向都应识别为同一专家。
pipeline.add_relation(conn, pid, n_eng, n_exp, pipeline.RELATION_ASK)
pipeline.add_relation(conn, pid, n_exp, n_eng, pipeline.RELATION_SUPPLY)
p = pipeline.get_pipeline(conn, pid)
asks = pipeline._ask_sources(conn, p["nodes"][1], p["relations"], p["nodes"])
check("ask 边取出可询问专家（双向均识别、去重为 1）",
      len(asks) == 1 and asks[0]["name"] == "射频硬件专家",
      f"asks={[a['name'] for a in asks]}")

# 允许集合裁决：名单外专家必须被拒（此判定发生在调用 LLM 之前）
fmea.set_allowed_experts(["射频硬件专家"])
r_out = actions.execute_builtin(conn, eng_id, "ask_expert",
                                {"expert": "嵌入式固件专家", "question": "?"})
check("问名单外专家被拒", not r_out.get("ok"), r_out.get("error", "")[:50])
r_self = actions.execute_builtin(conn, eng_id, "ask_expert",
                                 {"expert": "DFMEA 工程师", "question": "?"})
check("问自己被拒", not r_self.get("ok"))
fmea.set_allowed_experts(None)          # 复位（手动对话场景不受限）
fmea.push_ask(); fmea.push_ask()
r_deep = actions.execute_builtin(conn, eng_id, "ask_expert",
                                 {"expert": "射频硬件专家", "question": "?"})
fmea.pop_ask(); fmea.pop_ask()
check("超深度被拒（专家不可再问专家）", not r_deep.get("ok"),
      r_deep.get("error", "")[:50])

# ---- 清理 ----
pipeline.delete_pipeline(conn, pid)
left = conn.execute("SELECT COUNT(*) c FROM pipelines WHERE name=?",
                    ("_tmp_ask_verify",)).fetchone()["c"]
check("临时 pipeline 已清理", left == 0)

print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
