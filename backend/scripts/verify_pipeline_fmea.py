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
# 复核门只该拿到"表" —— 否则上游原文会把表挤到截断线外（实测判 FAIL 的原因）
rev = cm.allowed_kinds_for("DFMEA 复核员", list(cm.KINDS))
# 注意：KIND_CODE 是「直接产物」的通用豁免（见 allowed_kinds_for），会始终放行；
# 汇总节点的产出也常被判为「失效模式清单」，故清单与评分一并放行。
check("复核员只收 表/清单/评分（+代码豁免）",
      set(rev) <= {cm.KIND_DFMEA, cm.KIND_FAILURE_MODES, cm.KIND_SOD,
                   cm.KIND_CODE}, f"{rev}")
check("复核员白名单独立于汇总者（不含需求/任务原文）",
      cm.KIND_REQUIREMENT not in rev and cm.KIND_TASK not in rev, f"{rev}")
check("DFMEA 表入站预算足够放下整表",
      cm.KIND_INPUT_BUDGET[cm.KIND_DFMEA] >= 12000,
      f"{cm.KIND_INPUT_BUDGET[cm.KIND_DFMEA]}")

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

print("[4] E5 交付物健康检查（产出非交付物必须被识别）")
# 为什么必须测：实测 2026-09-13 —— 汇总节点把「工具调用草稿」当成最终答复
# 交出去（整段只有 <tool_calls>…</tool_calls>），pipeline 层**只要返回文本就往下游
# 传**，复核门拿到「无 DFMEA 表主体行」只能判 FAIL 并中止整条链路。
# 这一关不改写产出（那会违反「不介入生成」），只如实判定「这不算交付物」。
_LEAK = ('<tool_calls>\n<tool_call>{"name": "查询历史 FMEA", '
         '"args": {"part": "SW", "keyword": "固件"}}</tool_call>\n</tool_calls>')
check("整段工具调用草稿被识别为非交付物",
      bool(pipeline._deliverable_problem(_LEAK)),
      pipeline._deliverable_problem(_LEAK))
check("空产出被识别", bool(pipeline._deliverable_problem("")))
check("纯空白被识别", bool(pipeline._deliverable_problem("   \n  ")))
check("正常表格产出被判为可交付",
      not pipeline._deliverable_problem(
          "| 失效模式 | S | O | D | AP |\n|---|---|---|---|---|\n| 阻抗失配 | 7 | 5 | 5 | H |"))
check("正常复核结论被判为可交付",
      not pipeline._deliverable_problem("[REVIEW:PASS] 逐格核对完毕，来源可追溯。"))
# 边界：带标记但确有实质正文 → 不该误伤（否则会拖垮正常产出）
check("带标记但有实质正文不误伤",
      not pipeline._deliverable_problem(
          "分析如下：" + _LEAK + " 另外补充完整论述" + "内容" * 40))
check("自然语言提及工具名不误伤（无标记）",
      not pipeline._deliverable_problem("好的，我马上调用查询历史 FMEA 工具。"))

print("[5] E5 重试提示必须是**打包后的交接物字符串**（不能塞原始 dict）")
# 为什么必须测：2026-09-13 —— 重试提示我写成了 {"kind","content"} 的 dict，
# 而 inputs 实际是 pack_handoff 产出的 JSON 字符串，_run_nominate 会把它交给
# cm.shape_inputs → unpack_handoff，后者对 dict 调 .strip() 直接抛
# AttributeError，把**本该救回来的节点变成整条 job 崩**。
# 这是「改了热路径却没测异常路径」的同一类错误，第二次踩，故用断言钉住。
_hint = cm.pack_handoff(cm.KIND_GENERIC, "上一轮产出不是可交付物，请直接给正文。")
check("pack_handoff 产出字符串", isinstance(_hint, str))
_un = cm.unpack_handoff(_hint)
check("可被 unpack_handoff 还原", _un.get("kind") == cm.KIND_GENERIC
      and "不是可交付物" in (_un.get("content") or ""),
      f"kind={_un.get('kind')}")
_shaped = cm.shape_inputs([_hint])
check("可被 shape_inputs 正常消费", isinstance(_shaped, list) and len(_shaped) >= 1,
      f"{len(_shaped)} 组")
# 反向：dict 形态必须**不被接受**（钉住这个坑的存在）
try:
    cm.shape_inputs([{"kind": cm.KIND_GENERIC, "content": "x"}])
    check("dict 形态被拒（防止再犯）", False, "竟然没报错 —— 接口变了？")
except Exception as e:  # noqa: BLE001
    check("dict 形态被拒（防止再犯）", True, type(e).__name__)

print("[6] E6 复核门交草稿不得静默放行（UNKNOWN 与「没给结论」是两回事）")
# 为什么必须测：2026-09-13 run#17 —— 复核节点重试一次后**仍是**工具调用草稿
# （上下游上下文已 4.3 万字符），problem 被如实记录，但 _review_verdict 找不到
# [REVIEW:…] 标记 → 返回 UNKNOWN → **放行**，job 依然 done，复核门实际没执行。
# 这是"复核门形同虚设"最隐蔽的形态：日志齐、状态好、门没关。
_LEAK2 = '<tool_call>{"name": "查询历史 FMEA", "args": {"part": "蓝牙模块"}}</tool_call>'
check("草稿被 _deliverable_problem 识别", bool(pipeline._deliverable_problem(_LEAK2)))
check("草稿无结论 → verdict=UNKNOWN",
      pipeline._review_verdict(_LEAK2) == "UNKNOWN",
      pipeline._review_verdict(_LEAK2))
# 门控判据 = (problem 非空) and (草稿判定非空)：两者同时成立才拦（等价于
# 上面两个断言同时为真，这里显式钉住这个复合条件）。
_draft_gate = (bool(pipeline._deliverable_problem(_LEAK2))
               and pipeline._review_verdict(_LEAK2) == "UNKNOWN")
check("「草稿 + UNKNOWN」必须构成拦截条件", _draft_gate)
# 反向边界：复核员**真的**给了含糊结论（有正文、无标记）→ 仍放行（不误伤）
_vague = "我大致核对了一下，感觉没什么大问题，应该可以用。"
check("有正文的含糊结论不算草稿",
      not pipeline._deliverable_problem(_vague),
      pipeline._deliverable_problem(_vague))
check("有正文的含糊结论仍为 UNKNOWN 但不拦",
      pipeline._review_verdict(_vague) == "UNKNOWN"
      and not pipeline._deliverable_problem(_vague))

print("[7] E7 内置蓝图修订必须能同步进已实例化数字人（add_ontology update）")
# 为什么必须测：2026-09-13 —— 模板里「部件覆盖完整性」规则被改写并加长，
# 重新 seed 后**新规则进了库、被改动的规则定义纹丝不动**（仍是旧版）。
# 根因：add_ontology 对已存在的 (kind,name) 直接 return，从不更新 definition。
# 后果：平台升级内置蓝图后，已实例化的数字人永远拿不到规则修订 —— 静默失效。
from app import trainer  # noqa: E402
_tmp = conn.execute("SELECT id FROM identities ORDER BY id DESC LIMIT 1").fetchone()
_iid = _tmp["id"] if _tmp else None
if _iid:
    trainer.add_ontology(conn, _iid, "规则", "_t_update_probe", "第一版定义")
    trainer.add_ontology(conn, _iid, "规则", "_t_update_probe", "第二版定义",
                         update=True)
    _got = conn.execute(
        "SELECT definition FROM persona_ontology"
        " WHERE identity_id=? AND name='_t_update_probe'", (_iid,)).fetchone()
    check("update=True 改写了已存在条目的定义",
          (_got or {}).get("definition") == "第二版定义",
          str((_got or {}).get("definition")))
    check("update 只留一行（不是插入新行）",
          conn.execute("SELECT COUNT(*) c FROM persona_ontology"
                       " WHERE identity_id=? AND name='_t_update_probe'",
                       (_iid,)).fetchone()["c"] == 1)
    # 反向：不传 update 时必须保持「只补不改」的保守语义
    trainer.add_ontology(conn, _iid, "规则", "_t_update_probe", "第三版定义")
    _got2 = conn.execute(
        "SELECT definition FROM persona_ontology"
        " WHERE identity_id=? AND name='_t_update_probe'", (_iid,)).fetchone()
    check("不传 update 时定义不被覆盖（保守语义）",
          (_got2 or {}).get("definition") == "第二版定义",
          str((_got2 or {}).get("definition")))
    conn.execute("DELETE FROM persona_ontology WHERE identity_id=?"
                 " AND name='_t_update_probe'", (_iid,))
    conn.commit()

print("[8] E6 部件清单模糊匹配（产品名措辞不同不得漏检）")
# 为什么必须测：2026-09-13 —— 任务里产品名是「射频无线模块（蓝牙/WiFi）」，
# 知识库 product 登记为「WiFi 模块」，part_search 用 `product = ?` 精确匹配
# → count=0 → 整张 13 条部件知识库被跳过 → DFMEA 只覆盖 7/13 子系统。
# 修法：精确 → 子串互含 → token 重叠打分（≥0.5）→ 回传可用清单。
from app import fmea as _fmea  # noqa: E402
_r_exact = _fmea.part_search(conn, product="WiFi 模块")
check("精确产品名命中", (_r_exact.get("result") or {}).get("count", 0) >= 1,
      f"count={( _r_exact.get('result') or {}).get('count')}")
_r_variant = _fmea.part_search(conn, product="射频无线模块（蓝牙/WiFi）")
_v_count = (_r_variant.get("result") or {}).get("count", 0)
check("任务产品名（括号夹词）也能命中（token 重叠）", _v_count >= 1,
      f"count={_v_count} matched={(_r_variant.get('result') or {}).get('product')!r}")
_r_sub = _fmea.part_search(conn, product="手机 WiFi 模块")
check("含前后缀的产品名命中", (_r_sub.get("result") or {}).get("count", 0) >= 1,
      f"count={(_r_sub.get('result') or {}).get('count')}")
# 反向：完全不相关的产品名**不得**误命中，且必须回传可用清单供纠错
_r_miss = _fmea.part_search(conn, product="完全不相干的东西")
_mres = _r_miss.get("result") or {}
check("不相关产品名不误命中", _mres.get("count", 0) == 0,
      f"count={_mres.get('count')}")
check("不相关产品名回传可用清单 + hint（供纠错重查）",
      bool(_mres.get("products")) and bool(_mres.get("hint")),
      f"products={_mres.get('products')}")

# ---- 清理 ----
pipeline.delete_pipeline(conn, pid)
left = conn.execute("SELECT COUNT(*) c FROM pipelines WHERE name=?",
                    ("_tmp_ask_verify",)).fetchone()["c"]
check("临时 pipeline 已清理", left == 0)

# =====================================================================
print("[9] E8 LLM 出网路径：默认**直连**（自造 client 是风险源）")
# =====================================================================
# 2026-09-13 三组对照实测（每档 4~12 次连续流式调用，同一网络环境）：
#     no-client（SDK 自建、直连）       6/6   OK   6.5s   ✓ 最优
#     env-proxy（SDK 自建、走代理）     5/6   OK  34.9s   （慢 5 倍）
#     自定义 client: default(5s)        1/4   ✗
#     自定义 client: read=None          3/4
#     自定义 client: read=90s           3/4
#     shared client（复用连接池）        4/8   ✗
# 结论（与直觉相反）：① 手搭 httpx.Client 本身是风险源 —— 塞了 client，SDK 就
# 改用 client 的超时（httpx 默认 read=5s，流式首 token 慢一点就整条死）；②
# 直连是这里最快最稳的路径，`.env` 里「deepseek 必须走代理」是旧网络环境结论。
import httpx as _httpx  # noqa: E402
from app import llm as _llm  # noqa: E402

_saved_proxy = os.environ.get("LLM_PROXY")
# (a) 未配置代理 → 空 kwargs（把 client 交给 SDK 自建）
os.environ.pop("LLM_PROXY", None)
_k_direct = _llm._http_client_kwargs("https://api.deepseek.com")
check("未配置代理时不注入 client（返回空 kwargs，交 SDK 自建）",
      _k_direct == {}, f"got={list(_k_direct)}")
# (b) LLM_PROXY=off/none/- 也视作直连（显式关闭语义）
for _off in ("off", "none", "-", "OFF", " None "):
    os.environ["LLM_PROXY"] = _off
    check(f"LLM_PROXY={_off!r} 视作直连（不注入 client）",
          _llm._http_client_kwargs("https://api.deepseek.com") == {})
# (c) 显式配置代理时才自造 client，且**每次独立**（不共用池化连接）
os.environ["LLM_PROXY"] = "http://127.0.0.1:6789"
_k1 = _llm._http_client_kwargs("https://api.deepseek.com")
_k2 = _llm._http_client_kwargs("https://api.deepseek.com")
check("配置代理时产出 httpx.Client",
      isinstance(_k1.get("http_client"), _httpx.Client))
check("两次调用是**独立** client（不复用池化连接）",
      _k1.get("http_client") is not _k2.get("http_client"))
# 自造 client 必须带显式 read 超时（否则 httpx 默认 5s 会杀掉流式调用）
_t = _k1["http_client"].timeout
check("自造 client 的 read 超时不受 httpx 默认 5s 摆布",
      _t.read is None or float(_t.read) >= 60,
      f"read={_t.read}")
# 关闭语义：幂等 + 对空 kwargs 安全（调用方在 finally 里无条件调用）
_cli1 = _k1["http_client"]
_llm._close_client_kwargs(_k1)
check("_close_client_kwargs 关闭后 client.is_closed=True",
      getattr(_cli1, "is_closed", None) is True,
      f"is_closed={getattr(_cli1, 'is_closed', None)}")
_llm._close_client_kwargs(_k1)   # 二次关闭不得抛
check("_close_client_kwargs 幂等（二次关闭不抛）", True)
_llm._close_client_kwargs({})    # 直连路径 kwargs 为空
check("_close_client_kwargs 对空 kwargs 安全", True)
# (d) loopback（Ollama）必须绕开系统代理，否则 502
_k_local = _llm._http_client_kwargs("http://127.0.0.1:11434")
check("loopback 产出独立 client（trust_env=False，绕开系统代理）",
      isinstance(_k_local.get("http_client"), _httpx.Client)
      and _k_local.get("http_client") is not _k2.get("http_client"))
_llm._close_client_kwargs(_k_local)
_llm._close_client_kwargs(_k2)
if _saved_proxy is None:
    os.environ.pop("LLM_PROXY", None)
else:
    os.environ["LLM_PROXY"] = _saved_proxy

_llm._close_client_kwargs(_k_local)
_llm._close_client_kwargs(_k2)

print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
